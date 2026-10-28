"""Incremental discovery of InHire tenants (no-cost).

Candidates come from certificate transparency (crt.sh), the Internet Archive
(CDX), a seed file and a brute-force wordlist tested directly against the
public InHire API. They are filtered against a reserved-slug list, optionally
validated against the public InHire API and merged into
``data/inhire_companies.json``.

Two robustness features keep the daily job healthy:

- **TTL cache** for crt.sh / Wayback, so a flaky endpoint neither hammers the
  network every run nor loses previous results (falls back to stale cache).
- **Brute-force** source: tests candidate company slugs against
  ``GET /public/pages`` (``X-Tenant``); a 200 with ``jobsPage`` means the
  tenant really exists, independent of external services.

Pure helpers are dependency-light; network access happens only inside
``discover(...)`` / ``validate_tenant(...)`` / ``bruteforce_tenants(...)``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

API_BASE = "https://api.inhire.app/job-posts"
CRTSH_URL = "https://crt.sh/?q=%25.inhire.app&output=json"
WAYBACK_CDX = (
    "https://web.archive.org/cdx/search/cdx"
    "?url=*.inhire.app&output=json&fl=original&collapse=urlkey&limit=2000"
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

DEFAULT_COMPANIES_FILE = Path("data/inhire_companies.json")
DEFAULT_SEED_FILE = Path("data/inhire_seed.txt")
DEFAULT_BRUTEFORCE_FILE = Path("data/inhire_bruteforce.txt")
DEFAULT_CACHE_DIR = Path("data")
DEFAULT_CACHE_TTL = 86400  # 24h

# Subdomains that are infrastructure/internal, not companies.
RESERVED = {
    "www", "api", "auth", "app", "embed", "files", "cdn", "static", "status",
    "admin", "inhire-admin", "inhire", "inhub", "mcp", "mcp-dev", "plugin",
    "preview", "saml-setup", "sso-setup", "sso", "setup", "data-viz", "dev",
    "staging", "test", "tests", "demo", "mail", "email", "docs", "blog",
}

HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.inhire\.app$", re.IGNORECASE)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

# Built-in brute-force wordlist: Brazilian companies/startups commonly on InHire.
# Extend via ``data/inhire_bruteforce.txt`` (one slug per line).
BUILTIN_WORDLIST = [
    # Banks / fintech
    "nubank", "inter", "c6bank", "btg", "xp", "clear", "rico", "modalmais",
    "stone", "pagseguro", "cielo", "getnet", "willbank", "neon", "bs2",
    "creditas", "meutudo", "caju", "swile", "flash", "celcoin", "cora",
    "contabilizei", "livelo", "valecard", "bankme", "bancobs2", "bancopine",
    "bancotoyota", "breezyseguros", "heroseguros", "cashme", "cayena", "cerc",
    # Retail / marketplace / logistics
    "ifood", "magalu", "magazineluiza", "mercadolivre", "mercadopago", "olist",
    "vtex", "nuvemshop", "lojaintegrada", "tray", "yampi", "loggi", "rappi",
    "quintoandar", "loft", "housi", "imovelweb", "insiderstore", "mottu",
    "gobrax", "cappta", "appmax", "appfacilita", "paytrack", "solfacil",
    # Tech / software / consulting
    "totvs", "linx", "senior", "sankhya", "datasul", "ciandt", "stefanini",
    "thoughtworks", "globant", "epam", "accenture", "deloitte", "brq",
    "mazzatech", "luby", "pitang", "solutis", "venturus", "betha", "csu",
    "isystems", "nstech", "semantix", "qive", "olist", "cortex", "brivia",
    "lx", "dp6", "lwsa", "madeinweb", "lyncas", "zappts", "iconit", "bipa",
    # Health / education / other
    "alelo", "alice", "samaritano", "fleury", "einstein", "alura", "trybe",
    "rocketseat", "kenzie", "cubos", "descomplica", "estrategia", "grancursos",
    # Generic suffixes are NOT companies; they are filtered below.
]

# Generic/infra tokens that should never be treated as a company tenant.
_FALSE_POSITIVES = {
    "tech", "labs", "digital", "solutions", "systems", "software", "data",
    "ai", "ml", "cloud", "dev", "innovation", "ventures", "capital",
    "br", "com", "www", "app", "api", "admin", "status", "cdn", "static",
    "assets", "media", "files", "img", "images", "css", "js", "carreiras",
    "vagas", "jobs", "careers", "portal", "home", "site",
}


def slug_from_host(host: str) -> str | None:
    """Return the tenant slug from ``<slug>.inhire.app`` (else None)."""
    host = str(host or "").strip().lower().strip(".")
    m = HOST_RE.match(host)
    if not m:
        return None
    slug = m.group(1)
    if slug in RESERVED:
        return None
    return slug


def clean_slug(value: str) -> str | None:
    """Normalize a bare slug / host / URL fragment into a valid tenant slug."""
    text = str(value or "").strip().lower()
    if not text or text.startswith("#"):
        return None
    text = text.strip(".")
    if text.endswith(".inhire.app"):
        text = text[: -len(".inhire.app")]
    if "://" in text:
        text = re.sub(r"^https?://", "", text).split("/")[0]
        if text.endswith(".inhire.app"):
            text = text[: -len(".inhire.app")]
    if not SLUG_RE.match(text) or text in RESERVED or text in _FALSE_POSITIVES:
        return None
    return text


def parse_crtsh(payload) -> set[str]:
    slugs: set[str] = set()
    if not isinstance(payload, list):
        return slugs
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        names = str(entry.get("name_value") or "").splitlines()
        names.append(str(entry.get("common_name") or ""))
        for name in names:
            slug = slug_from_host(name.strip().lstrip("*."))
            if slug:
                slugs.add(slug)
    return slugs


def parse_wayback(payload) -> set[str]:
    slugs: set[str] = set()
    if not isinstance(payload, list):
        return slugs
    for row in payload[1:] if payload and isinstance(payload[0], list) else payload:
        original = row[0] if isinstance(row, list) and row else row
        if not isinstance(original, str):
            continue
        m = re.match(r"https?://([^/]+)", original)
        if not m:
            continue
        slug = slug_from_host(m.group(1))
        if slug:
            slugs.add(slug)
    return slugs


def parse_seed(text: str) -> set[str]:
    slugs: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slug = slug_from_host(f"{line.lower()}.inhire.app")
        if slug:
            slugs.add(slug)
    return slugs


def parse_bruteforce(text: str) -> list[str]:
    """Parse a brute-force wordlist (one slug per line, ``#`` comments)."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        slug = clean_slug(line)
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def merge_slugs(*collections) -> list[str]:
    out: set[str] = set()
    for collection in collections:
        for slug in collection or []:
            slug = str(slug or "").strip().lower()
            if slug and slug not in RESERVED:
                out.add(slug)
    return sorted(out)


def validate_tenant(slug: str, session, timeout: int = 20) -> bool:
    try:
        resp = session.get(
            f"{API_BASE}/public/pages",
            headers={"X-Tenant": slug, "Accept": "application/json"},
            timeout=timeout,
        )
        return resp.status_code == 200 and isinstance(resp.json(), dict)
    except Exception:  # noqa: BLE001
        return False


def _get_json(session, url: str, timeout: int = 60, retries: int = 3) -> object:
    """GET that tolerates rate-limit/HTML responses by retrying with backoff."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                text = resp.text.lstrip()
                if text.startswith(("[", "{")):
                    return resp.json()
                last_error = "resposta não-JSON (possível rate limit)"
            else:
                last_error = f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
        if attempt < retries:
            time.sleep(2 * attempt)
    raise RuntimeError(str(last_error))


# ------------------------------------------------------------------- caching


def _cache_path(name: str, cache_dir: Path | str | None) -> Path:
    return Path(cache_dir or DEFAULT_CACHE_DIR) / f"inhire_{name}_cache.json"


def _load_cache(path: Path) -> tuple[float, list[str]] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return float(data.get("ts", 0)), list(data.get("slugs", []))
    except Exception:  # noqa: BLE001
        return None


def _save_cache(path: Path, slugs) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps({"ts": time.time(), "slugs": sorted(slugs)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _cached_source(session, url, name, parse_fn, cache_dir, cache_ttl, use_cache) -> set[str]:
    """Fetch ``url`` and parse it, using a TTL cache with stale fallback."""
    path = _cache_path(name, cache_dir)
    if use_cache and cache_ttl > 0 and path.is_file():
        cached = _load_cache(path)
        if cached and (time.time() - cached[0]) < cache_ttl:
            print(f"   {name}: cache hit ({len(cached[1])})")
            return set(cached[1])
    try:
        slugs = parse_fn(_get_json(session, url))
        if slugs:
            _save_cache(path, slugs)
        return slugs
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ {name} falhou: {e}")
        cached = _load_cache(path) if path.is_file() else None
        if cached:
            print(f"   {name}: usando cache obsoleto ({len(cached[1])})")
            return set(cached[1])
        return set()


# ---------------------------------------------------------------- bruteforce


def load_bruteforce_candidates(
    bruteforce_file: Path | str | None = DEFAULT_BRUTEFORCE_FILE,
) -> list[str]:
    """Built-in wordlist + optional ``data/inhire_bruteforce.txt`` (deduped)."""
    candidates = list(BUILTIN_WORDLIST)
    if bruteforce_file and Path(bruteforce_file).is_file():
        try:
            candidates = parse_bruteforce(
                Path(bruteforce_file).read_text(encoding="utf-8")
            ) + candidates
        except Exception:  # noqa: BLE001
            pass
    seen: set[str] = set()
    out: list[str] = []
    for raw in candidates:
        slug = clean_slug(raw)
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def bruteforce_tenants(
    session,
    candidates,
    *,
    limit: int = 250,
    request_gap: float = 0.3,
    timeout: int = 20,
) -> list[str]:
    """Test candidate slugs directly against the public API.

    A tenant is valid when ``GET /public/pages`` (``X-Tenant``) returns 200 with
    a JSON object (``jobsPage``). Independent of crt.sh/Wayback availability.
    """
    found: list[str] = []
    todo = [clean_slug(c) for c in (candidates or [])]
    todo = [s for s in todo if s][:limit]
    total = len(todo)
    for i, slug in enumerate(todo, 1):
        if validate_tenant(slug, session, timeout=timeout):
            found.append(slug)
        if i % 25 == 0:
            print(f"   bruteforce {i}/{total} ({len(found)} ok)")
        time.sleep(request_gap)
    print(f"   bruteforce: {len(found)}/{total} válidos")
    return found


def discover(
    companies_file: Path = DEFAULT_COMPANIES_FILE,
    seed_file: Path = DEFAULT_SEED_FILE,
    validate: bool = True,
    request_gap: float = 0.3,
    *,
    use_cache: bool = True,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    cache_dir: Path | str | None = DEFAULT_CACHE_DIR,
    bruteforce: bool = True,
    bruteforce_limit: int = 250,
    bruteforce_file: Path | str | None = DEFAULT_BRUTEFORCE_FILE,
) -> list[str]:
    """Discover tenants and merge into ``companies_file``. Returns the list."""
    import requests

    existing: list[str] = []
    if Path(companies_file).is_file():
        try:
            data = json.loads(Path(companies_file).read_text(encoding="utf-8"))
            existing = data.get("companies", []) if isinstance(data, dict) else data
        except Exception:  # noqa: BLE001
            existing = []

    seed_slugs: set[str] = set()
    if Path(seed_file).is_file():
        seed_slugs = parse_seed(Path(seed_file).read_text(encoding="utf-8"))

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    discovered: set[str] = set()
    pre_validated: set[str] = set()
    try:
        discovered |= _cached_source(
            session, CRTSH_URL, "crtsh", parse_crtsh, cache_dir, cache_ttl, use_cache
        )
        discovered |= _cached_source(
            session, WAYBACK_CDX, "wayback", parse_wayback, cache_dir, cache_ttl, use_cache
        )

        known = set(merge_slugs(existing, seed_slugs, discovered))
        if bruteforce:
            bf_candidates = [
                s for s in load_bruteforce_candidates(bruteforce_file) if s not in known
            ]
            pre_validated |= set(
                bruteforce_tenants(
                    session,
                    bf_candidates,
                    limit=bruteforce_limit,
                    request_gap=request_gap,
                )
            )
            discovered |= pre_validated
    finally:
        pass

    candidates = merge_slugs(existing, seed_slugs, discovered)
    print(
        f"🔎 {len(candidates)} candidatos (existentes={len(existing)}, "
        f"seed={len(seed_slugs)}, descobertos={len(discovered)})"
    )

    if validate:
        valid: list[str] = []
        for i, slug in enumerate(candidates, 1):
            if slug in pre_validated:
                valid.append(slug)
                continue
            if validate_tenant(slug, session):
                valid.append(slug)
            if i % 25 == 0:
                print(f"   validados {i}/{len(candidates)} ({len(valid)} ok)")
            time.sleep(request_gap)
        candidates = valid
        print(f"✅ {len(candidates)} tenants válidos")

    Path(companies_file).parent.mkdir(parents=True, exist_ok=True)
    disabled = []
    try:
        previous = json.loads(Path(companies_file).read_text(encoding="utf-8"))
        if isinstance(previous, dict):
            disabled = [d for d in (previous.get("disabled") or []) if d in candidates]
    except Exception:  # noqa: BLE001
        disabled = []
    Path(companies_file).write_text(
        json.dumps({"companies": candidates, "disabled": disabled}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    session.close()
    return candidates


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Descobre tenants InHire (crt.sh + Wayback + bruteforce).")
    ap.add_argument("--no-validate", action="store_true", help="Não valida via API (mais rápido/ruidoso).")
    ap.add_argument("--no-bruteforce", action="store_true", help="Desliga a fonte bruteforce.")
    ap.add_argument("--bruteforce-limit", type=int, default=250, help="Máx. de slugs testados no bruteforce.")
    ap.add_argument("--bruteforce-file", default=str(DEFAULT_BRUTEFORCE_FILE), help="Wordlist extra.")
    ap.add_argument("--no-cache", action="store_true", help="Ignora o cache TTL (força rede).")
    ap.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL, help="TTL do cache em segundos.")
    ap.add_argument("--file", default=str(DEFAULT_COMPANIES_FILE))
    args = ap.parse_args()
    result = discover(
        companies_file=Path(args.file),
        validate=not args.no_validate,
        use_cache=not args.no_cache,
        cache_ttl=args.cache_ttl,
        bruteforce=not args.no_bruteforce,
        bruteforce_limit=args.bruteforce_limit,
        bruteforce_file=Path(args.bruteforce_file),
    )
    print(f"Gravados {len(result)} tenants em {args.file}")
