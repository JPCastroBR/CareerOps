"""Collector for InHire career portals (``https://<slug>.inhire.app``).

Public, no-auth API (discovered in the portal bundle):

- List:   ``GET {API}/public/pages``            header ``X-Tenant: <slug>``
- Detail: ``GET {API}/public/pages/{job_id}``   header ``X-Tenant: <slug>``
- Job URL: ``https://<slug>.inhire.app/vagas/<job_id>/<slug>``

Pure parsing helpers (``parse_listing``, ``build_row``, ``is_remote``,
``slugify``) are dependency-light so they can be unit-tested in isolation.
``fetch_inhire_jobs`` imports ``requests``/``pandas`` lazily.
"""
from __future__ import annotations

import re
import time
import unicodedata

API_BASE = "https://api.inhire.app/job-posts"
PORTAL_BASE = "https://{slug}.inhire.app"
JOB_PATH = "/vagas/{job_id}/{job_slug}"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# workplaceType values returned by InHire -> remote?
_REMOTE_TYPES = {"remote", "remoto", "home office", "home-office", "homeoffice"}

# Default title pre-filter (applied before fetching details). Empty = fetch all.
DEFAULT_INCLUDE = [
    "sre", "site reliability", "reliability", "devops", "devsecops",
    "platform", "plataforma", "infrastructure", "infraestrutura", "cloud",
    "nuvem", "observability", "observabilidade", "monitoring", "monitoramento",
    "linux", "kubernetes", "terraform", "ansible", "automation", "automação",
    "sistemas", "systems", "dados", "data", "security", "segurança",
    "network", "redes", "banco de dados", "database",
]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = TAG_RE.sub("\n", raw)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return WS_RE.sub(" ", text).strip()


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9 ]", "", text).strip().lower()
    return re.sub(r"\s+", "-", text) or "vaga"


def is_remote(workplace_type) -> bool:
    return str(workplace_type or "").strip().lower() in _REMOTE_TYPES


def _include_title(title: str, include: list[str]) -> bool:
    if not include:
        return True
    t = str(title or "").lower()
    return any(k in t for k in include)


def parse_listing(payload: dict) -> list[dict]:
    """Extract the public job list from ``/public/pages``."""
    jobs = (payload or {}).get("jobsPage") or []
    out = []
    for j in jobs:
        if not j.get("jobId"):
            continue
        out.append({
            "job_id": j.get("jobId"),
            "title": j.get("displayName") or "",
            "workplace_type": j.get("workplaceType") or "",
            "location": j.get("location") or "",
            "career_page": j.get("careerPageId") or "default",
        })
    return out


def build_row(company: str, slug: str, listing: dict, detail: dict) -> dict:
    """Build a job row (banks-compatible schema) from listing + detail."""
    detail = detail or {}
    job_id = detail.get("jobId") or listing.get("job_id")
    title = detail.get("displayName") or listing.get("title") or ""
    workplace = detail.get("workplaceType") or listing.get("workplace_type") or ""
    remote = is_remote(workplace)
    location = detail.get("location") or listing.get("location") or ""
    complement = str(detail.get("locationComplement") or "").strip()
    if complement:
        location = f"{location} ({complement})" if location else complement

    contract = detail.get("contractType")
    if isinstance(contract, list):
        job_type = ", ".join(str(c) for c in contract)
    else:
        job_type = str(contract or "")

    url = f"{PORTAL_BASE.format(slug=slug)}{JOB_PATH.format(job_id=job_id, job_slug=slugify(title))}"
    return {
        "title": title,
        "company": company,
        "location": location,
        "job_url": url,
        "description": strip_html(detail.get("description") or ""),
        "job_type": job_type,
        "job_level": "",
        "is_remote": remote,
        "remote": "remote" if remote else "not_remote",
        "date_posted": detail.get("publishedAt") or "",
        "date_valid_through": "",
        "source": f"inhire:{slug}",
    }


def _get_json(session, url: str, headers: dict, *, timeout: int = 30,
              retries: int = 3, backoff: float = 1.5):
    """GET JSON with retry/backoff. Returns None on 404/410 (tenant inexistente)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (404, 410):
                return None
            last = f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise RuntimeError(str(last))


def fetch_inhire_jobs(
    companies: list[str],
    include_keywords: list[str] | None = None,
    results_per_company: int = 30,
    request_gap: float = 0.6,
    session=None,
    timeout: int = 30,
    retries: int = 3,
    net_fail_limit: int = 3,
) -> "object":
    """Fetch jobs for each InHire tenant slug.

    Returns a ``pandas.DataFrame`` with the same columns as ``banks.py`` so the
    rows flow through the existing blocklist / dedup / modality filters.

    Includes a circuit-breaker: after ``net_fail_limit`` consecutive network
    failures the pass is aborted (avoids hammering the API during an outage).
    """
    import pandas as pd
    import requests

    include = [k.lower() for k in (include_keywords or DEFAULT_INCLUDE)]
    owns_session = session is None
    if owns_session:
        session = requests.Session()
        session.headers.update({"User-Agent": UA})

    consec_fail = 0

    columns = [
        "title", "company", "location", "job_url", "description",
        "job_type", "job_level", "is_remote", "remote", "date_posted",
        "date_valid_through", "source",
    ]
    rows: list[dict] = []
    for slug in companies:
        slug = str(slug or "").strip()
        if not slug:
            continue
        try:
            listing = _get_json(
                session,
                f"{API_BASE}/public/pages",
                {"X-Tenant": slug, "Accept": "application/json"},
                timeout=timeout,
                retries=retries,
            )
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            print(f"🏢 [{slug}] listagem falhou: {e}")
            if consec_fail >= net_fail_limit:
                print(f"⚠️ InHire: {consec_fail} falhas seguidas — rede aparenta fora do ar; "
                      f"abortando pass desta execução.")
                break
            continue
        if not isinstance(listing, dict):
            consec_fail = 0
            print(f"🏢 [{slug}] tenant inexistente (404) — ignorado")
            continue
        consec_fail = 0

        company = listing.get("tenantName") or slug
        jobs = parse_listing(listing)
        candidates = [j for j in jobs if _include_title(j["title"], include)]
        candidates = candidates[:results_per_company]
        print(f"🏢 [{company}] {len(jobs)} vagas públicas, {len(candidates)} candidatas a detalhe")

        for item in candidates:
            try:
                detail = _get_json(
                    session,
                    f"{API_BASE}/public/pages/{item['job_id']}",
                    {"X-Tenant": slug, "Accept": "application/json"},
                    timeout=timeout,
                    retries=retries,
                )
            except Exception as e:  # noqa: BLE001
                print(f"   ↳ ⚠️ detalhe {item['job_id']} falhou: {e}")
                continue
            if not isinstance(detail, dict):
                continue
            rows.append(build_row(company, slug, item, detail))
            time.sleep(request_gap)

    if owns_session:
        session.close()
    return pd.DataFrame(rows, columns=columns)
