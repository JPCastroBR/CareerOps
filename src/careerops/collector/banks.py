"""Collector for Brazilian banks' career portals.

Supported sources (all no-auth):
- `gupy`:    Next.js pages embed the job list + full details in `__NEXT_DATA__`
             (Itaú, Sicredi, BMG, ...).
- `workday`: Workday job boards expose a public cxs JSON search API and each
             job page embeds a full `application/ld+json` payload
             (Santander, ...).

Output schema matches what `main.py` expects from JobSpy so bank jobs flow
through the same blocklist / anti-dup / n8n stage.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx
import pandas as pd
import requests

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# ---------------------------------------------------------------------------
# Gupy (itau/sicredi/bmg, ...)
# ---------------------------------------------------------------------------
GUPY_LIST_URL = "https://{subdomain}.gupy.io/"
GUPY_DETAIL_URL = "https://{subdomain}.gupy.io/jobs/{job_id}?jobBoardSource=gupy_public_page"

# ---------------------------------------------------------------------------
# Workday (santander, ...)
# ---------------------------------------------------------------------------
WD_SEARCH_URL = "https://{host}/wday/cxs/{tenant}/{site}/jobs"
WD_JOB_URL = "https://{host}/pt-BR/{site}{external_path}"

# Composite keywords used as `searchText` on Workday. Each term is its own
# search pass (Workday text search is a single phrase). Kept small: the cxs
# API throttles bursts (HTTP 400 for requests above its short-window quota).
WD_SEARCH_TERMS = [
    "SRE", "Site Reliability", "DevOps", "Platform Engineer",
    "Infrastructure", "Cloud", "Observability",
]

# Light "is this tech/ops relevant?" pre-filter applied to titles BEFORE
# fetching full job details (avoids paging through non-tech vacancies).
DEFAULT_INCLUDE = [
    "sre", "site reliability", "reliability",
    "devops", "platform engineer", "engenharia de plataforma", "engenheiro(a) de plataforma",
    "infrastructure", "infraestrutura", "cloud", "nuvem",
    "devsecops", "secops", "segurança da informação",
    "observabilidade", "observability", "monitoramento", "monitoring",
    "automação", "automation", "linux", "cybersecurity", "cyber security",
    "plataforma", "squad", "backstage", "kubernetes", "docker", "terraform",
    "aws", "azure", "gcp", "oci", "ansible", "grafana", "datadog",
    "sistema", "sistemas", "engineering",
]

GUPY_TO_REMOTE = {
    "remote": True, "remoto": True,
    "hybrid": False, "hibrido": False, "híbrido": False,
    "on-site": False, "on_site": False, "presencial": False, "presential": False,
}

# Workday has no workplaceType; detect remote from title/location/description.
WD_REMOTE_MARKERS = ["remoto", "remote", "teletrabalho", "home office", "work from home", "homebased"]
WD_HYBRID_ONSITE = ["híbrido", "hibrido", "hybrid", "presencial", "on-site", "onsite", "escritório", "office"]

# locationsText fragments that look like Brazil (used to pre-filter before
# fetching full detail pages).
BR_LOCATION_RE = re.compile(
    r"sao paulo|\bsp\b|rio de janeiro|\brj\b|brasilia|brasília|belo horizonte|\bbh\b|curitiba|"
    r"porto alegre|recife|salvador|campinas|santos|osasco|guarulhos|sorocaba|ribeirao|são bernardo|"
    r"santo andré|sao leopoldo|joinville|florianópolis|florianopolis|blumenau|chuí|"
    r"brazil|brasil",
    re.IGNORECASE,
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


@dataclass
class BankTarget:
    name: str
    subdomain: str
    source: str = "gupy"
    workday: dict | None = None


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = TAG_RE.sub("\n", raw)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return WS_RE.sub(" ", text).strip()


def _get(session: requests.Session, url: str, **kw):
    resp = session.get(url, timeout=30, **kw)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} em {url}")
    return resp


def _parse_next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ não encontrado")
    return json.loads(m.group(1))


def _is_remote_text(text: str, strict: bool = False) -> bool:
    """Remote heuristic used by Workday (no workplaceType field exists)."""
    tl = text.lower()
    has_remote = any(k in tl for k in WD_REMOTE_MARKERS)
    has_onsite = any(k in tl for k in WD_HYBRID_ONSITE)
    if strict:
        return has_remote and not has_onsite
    return has_remote


class _WDRateGate:
    """Keeps Workday cxs requests under its short-window quota.

    Workday returns HTTP 400 for requests fired above ~6-8 per ~60s (per IP).
    Maintains a rolling window: blocks until at least one slot frees and a
    minimum inter-request gap has passed. Used by the whole Workday crawl.
    """

    def __init__(self, max_per_window: int = 8, window: float = 60.0,
                 min_interval: float = 2.5):
        self.max_per_window = max_per_window
        self.window = window
        self.min_interval = min_interval
        self._stamps: list[float] = []
        self._last: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        self._stamps = [s for s in self._stamps if now - s < self.window]
        if len(self._stamps) >= self.max_per_window:
            need = now - self._stamps[0] + 1.0
            if need > 0:
                time.sleep(need)
                now = time.monotonic()
                self._stamps = [s for s in self._stamps if now - s < self.window]
        gap = self.min_interval - (now - self._last)
        if gap > 0:
            time.sleep(gap)
            now = time.monotonic()
        self._last = now
        self._stamps.append(now)


# ---------------------------------------------------------------------------
# Gupy source
# ---------------------------------------------------------------------------
def _gu_fetch_job_list(session: requests.Session, target: BankTarget) -> tuple[list[dict], int | None]:
    html = _get(session, GUPY_LIST_URL.format(subdomain=target.subdomain)).text
    props = _parse_next_data(html).get("props", {}).get("pageProps", {})
    jobs = props.get("jobs") or []
    company_id = props.get("careerPage", {}).get("companyId")
    return jobs, company_id


def _gu_fetch_job_detail(session: requests.Session, target: BankTarget, job_id: int) -> dict:
    html = _get(session, GUPY_DETAIL_URL.format(subdomain=target.subdomain, job_id=job_id)).text
    job = _parse_next_data(html).get("props", {}).get("pageProps", {}).get("job", {})
    if not job:
        raise ValueError(f"job {job_id} sem detalhe")
    return job


def _gu_location(detail: dict, list_item: dict) -> str:
    for src in (detail, list_item.get("workplace", {})):
        if not isinstance(src, dict):
            continue
        addr = src.get("address")
        if isinstance(addr, dict):
            parts = [p for p in (addr.get("city"), addr.get("state"), addr.get("country")) if p]
            if parts:
                return ", ".join(parts)
    return "Brasil"


def _gu_is_remote(item: dict) -> bool:
    wp = item.get("workplace") if isinstance(item, dict) else None
    if isinstance(wp, dict):
        item = wp
    wtype = str(item.get("workplaceType", "")).lower()
    return GUPY_TO_REMOTE.get(wtype, False)


def _run_gupy(session: requests.Session, target: BankTarget, include: list[str],
              results_wanted: int, remote_only_br: bool) -> list[dict]:
    jobs, _ = _gu_fetch_job_list(session, target)
    candidates = []
    for j in jobs:
        title = str(j.get("title") or "")
        if remote_only_br and not _gu_is_remote(j):
            continue
        if not _include_title(title, include):
            continue
        candidates.append(j)

    fetched = min(results_wanted, len(candidates))
    print(f"🏦 [{target.name}] {len(jobs)} vagas na lista, {fetched} candidatas a detalhe")
    rows = []
    for j in candidates[:fetched]:
        job_id = j.get("id")
        try:
            detail = _gu_fetch_job_detail(session, target, int(job_id))
        except Exception as e:  # noqa: BLE001
            print(f"   ↳ ⚠️ detalhe {job_id} falhou: {e}")
            continue
        title_d = str(detail.get("name") or j.get("title") or "")
        desc = "\n".join(
            _strip_html(detail.get(k) or "")
            for k in ("description", "prerequisites", "responsibilities")
            if detail.get(k)
        )
        remote = _gu_is_remote(detail)
        if remote_only_br and not remote:
            continue
        rows.append({
            "title": title_d,
            "company": target.name,
            "location": _gu_location(detail, j),
            "job_url": GUPY_DETAIL_URL.format(subdomain=target.subdomain, job_id=job_id),
            "description": desc,
            "job_type": str(detail.get("jobType") or j.get("type") or ""),
            "job_level": "",
            "is_remote": remote,
            "remote": "remote" if remote else "not_remote",
            "date_posted": detail.get("publishedAt"),
            "date_valid_through": detail.get("expiresAt"),
            "source": f"gupy:{target.subdomain}",
        })
    return rows


# ---------------------------------------------------------------------------
# Workday source
# ---------------------------------------------------------------------------
def _wd_search(client: httpx.Client, gate: _WDRateGate, target: BankTarget,
               terms: list[str], per_term: int = 20) -> list[dict]:
    url = WD_SEARCH_URL.format(host=target.subdomain,
                               tenant=target.workday["tenant"],
                               site=target.workday["site"])
    found: dict[str, dict] = {}
    broken = False
    for term in terms:
        if broken:
            break
        body = {"appliedFacets": {}, "limit": per_term, "offset": 0, "searchText": term}
        try:
            data = _wd_req(client, gate, target, "POST", url, json=body).json()
        except Exception as e:  # noqa: BLE001
            print(f"   ↳ ⚠️ search '{term}' falhou: {e}")
            # Throttle prolongado: para de mandar novos termos pra IP recuperar.
            broken = True
            continue
        for p in data.get("jobPostings", []):
            path = p.get("externalPath", "")
            if not path:
                continue
            found.setdefault(path, p)
    return list(found.values())


def _wd_fetch_detail(client: httpx.Client, gate: _WDRateGate, target: BankTarget,
                     external_path: str) -> dict:
    resp = _wd_req(client, gate, target, "GET", WD_JOB_URL.format(
        host=target.subdomain, site=target.workday["site"], external_path=external_path))
    html = resp.text
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("@type") and data.get("title"):
            return data
    raise ValueError("JSON-LD job não encontrado")


_WD_BACKOFF = 60.0


def _wd_req(client: httpx.Client, gate: _WDRateGate, target: BankTarget,
            method: str, url: str, **kw) -> httpx.Response:
    """Paced + throttled request. On 4xx/5xx waits and retries once."""
    for attempt in (1, 2):
        gate.wait()
        try:
            resp = client.request(method, url, **kw)
        except httpx.HTTPError as e:
            if attempt == 2:
                raise
            print(f"   ↳ ⚠️ Workday erro de rede ({e}), tentando de novo em {_WD_BACKOFF:.0f}s")
            time.sleep(_WD_BACKOFF)
            continue
        if resp.status_code < 400:
            return resp
        if attempt == 2:
            raise RuntimeError(f"Workday HTTP {resp.status_code} em {url}")
        print(f"   ↳ ⚠️ Workday HTTP {resp.status_code} (throttle), aguardo {_WD_BACKOFF:.0f}s e re-tento")
        time.sleep(_WD_BACKOFF)
    raise RuntimeError("Workday request sem retorno")  # pragma: no cover


def _wd_job_location(detail: dict) -> str:
    addr = (detail.get("jobLocation") or {}).get("address") or {}
    loc = str(addr.get("addressLocality") or "")
    country = str(addr.get("addressCountry") or "")
    country = {"Brasil": "Brazil"}.get(country, country)
    parts = [p for p in (loc, country) if p]
    return ", ".join(parts) or "Brasil"


def _run_workday(client: httpx.Client, gate: _WDRateGate, target: BankTarget,
                 include: list[str], results_wanted: int, remote_only_br: bool) -> list[dict]:
    rows = []
    try:
        listings = _wd_search(client, gate, target, WD_SEARCH_TERMS)
    except Exception as e:  # noqa: BLE001
        print(f"❌ [{target.name}] search Workday falhou: {e}")
        return rows

    # Pre-filtro por localização BR (evita baixar páginas do mundo inteiro).
    br_candidates = [p for p in listings if BR_LOCATION_RE.search(str(p.get("locationsText", "")))]
    print(f"🏦 [{target.name}] {len(listings)} vagas no search, {len(br_candidates)} com localização BR")

    fetched = 0
    for p in br_candidates:
        if fetched >= results_wanted:
            break
        path = p["externalPath"]
        title = str(p.get("title") or "")
        try:
            detail = _wd_fetch_detail(client, gate, target, path)
        except Exception as e:  # noqa: BLE001
            print(f"   ↳ ⚠️ detalhe falhou: {e}")
            continue
        if not BR_LOCATION_RE.search(str((detail.get("jobLocation") or {}).get("address", {}).get("addressCountry", ""))):
            continue
        title_d = str(detail.get("title") or title or "")
        if not _include_title(title_d, include):
            continue

        desc_html = detail.get("description") or ""
        desc = _strip_html(desc_html) if desc_html else ""
        marker_text = f"{title_d} {_wd_job_location(detail)} {desc[:1200]}"
        remote = _is_remote_text(marker_text)
        if remote_only_br and not remote:
            continue

        company = target.name
        org = detail.get("hiringOrganization")
        if isinstance(org, dict) and org.get("name"):
            company = str(org["name"])

        rows.append({
            "title": title_d,
            "company": company,
            "location": _wd_job_location(detail),
            "job_url": WD_JOB_URL.format(host=target.subdomain, site=target.workday["site"], external_path=path),
            "description": desc,
            "job_type": str(detail.get("employmentType") or ""),
            "job_level": "",
            "is_remote": remote,
            "remote": "remote" if remote else "not_remote",
            "date_posted": detail.get("datePosted"),
            "date_valid_through": detail.get("validThrough"),
            "source": f"workday:{target.subdomain}",
        })
        fetched += 1
    return rows


# ---------------------------------------------------------------------------
# Dispatcher / public API
# ---------------------------------------------------------------------------
def _include_title(title: str, include: list[str]) -> bool:
    t = (title or "").lower()
    return any(k in t for k in include)


def fetch_bank_jobs(
    banks: list[dict],
    include_keywords: list[str] | None = None,
    results_wanted: int = 50,
    remote_only_br: bool = True,
) -> pd.DataFrame:
    include = include_keywords or DEFAULT_INCLUDE
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})

    rows: list[dict] = []
    wd_client: httpx.Client | None = None
    wd_gate = _WDRateGate()
    for bank in banks:
        name = bank.get("name") or bank.get("subdomain") or "Banco"
        subdomain = str(bank.get("subdomain", "")).strip()
        source = (bank.get("source") or "gupy").lower()
        if not subdomain:
            print(f"🏦 Banco '{name}' sem subdomain — pulando.")
            continue

        budget = min(int(bank.get("results", results_wanted)), results_wanted)
        target = BankTarget(name=name, subdomain=subdomain, source=source)
        if source == "workday":
            wd = bank.get("workday") or {}
            tenant = str(wd.get("tenant") or "")
            site = str(wd.get("site") or "")
            if not tenant or not site:
                print(f"🏦 Banco '{name}' source=workday sem tenant/site — pulando.")
                continue
            target.workday = {"host": subdomain, "tenant": tenant, "site": site}
            if wd_client is None:
                wd_client = httpx.Client(http2=True, timeout=45)
                wd_client.headers.update({"User-Agent": UA})
            rows.extend(_run_workday(wd_client, wd_gate, target, include, budget, remote_only_br))
        elif source == "gupy":
            rows.extend(_run_gupy(session, target, include, budget, remote_only_br))
        else:
            print(f"🏦 Banco '{name}' usa fonte '{source}' (não implementada) — pulando.")

    if wd_client is not None:
        wd_client.close()

    return pd.DataFrame(rows, columns=[
        "title", "company", "location", "job_url", "description",
        "job_type", "job_level", "is_remote", "remote", "date_posted",
        "date_valid_through", "source",
    ])