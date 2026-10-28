"""Read-only Google Sheets connector for the dashboard.

Reads the ``Jobsearch-spy`` spreadsheet (tabs ``Jobs``, ``Rejected-Jobs``,
``Salario``) with a service account and a short TTL cache, so the dashboard can
render the analyzed jobs nicely without hammering the API.

Settings resolution (first match wins per key):

1. environment variables (``CAREEROPS_SHEET_ID``, ``GOOGLE_SA_FILE`` …)
2. ``secrets/sheets.json`` (gitignored)
3. ``config.json`` → ``sheets``

Credentials are never logged.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
DEFAULT_CACHE_TTL = 60
_TAB_KEYS = {
    "jobs": "jobs_tab",
    "rejected": "rejected_tab",
    "salary": "salary_tab",
}

_cache: dict[str, tuple[float, dict]] = {}
_creds = None


def _load_settings() -> dict:
    settings: dict = {}
    secret_path = Path(os.environ.get("CAREEROPS_SHEETS_CONFIG", "secrets/sheets.json"))
    if secret_path.is_file():
        try:
            settings.update(json.loads(secret_path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    if not settings.get("spreadsheet_id"):
        try:
            cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
            settings.update(cfg.get("sheets") or {})
        except Exception:  # noqa: BLE001
            pass

    env_map = {
        "CAREEROPS_SHEET_ID": "spreadsheet_id",
        "GOOGLE_SA_FILE": "service_account",
        "CAREEROPS_JOBS_TAB": "jobs_tab",
        "CAREEROPS_REJECTED_TAB": "rejected_tab",
        "CAREEROPS_SALARY_TAB": "salary_tab",
    }
    for env, key in env_map.items():
        if os.environ.get(env):
            settings[key] = os.environ[env]

    settings.setdefault("service_account", "secrets/google-sa.json")
    settings.setdefault("jobs_tab", "Jobs")
    settings.setdefault("rejected_tab", "Rejected-Jobs")
    settings.setdefault("salary_tab", "Salario")
    settings.setdefault("cache_ttl", DEFAULT_CACHE_TTL)
    return settings


def _get_token(sa_path: str) -> str:
    global _creds
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    if _creds is None:
        _creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    if not _creds.valid:
        _creds.refresh(Request())
    return _creds.token


def _rows_to_dicts(values: list[list[str]]) -> tuple[list[str], list[dict]]:
    if not values:
        return [], []
    headers = [str(h).strip() for h in values[0]]
    rows: list[dict] = []
    for raw in values[1:]:
        if not any(str(c).strip() for c in raw):
            continue
        row = {}
        for i, head in enumerate(headers):
            row[head] = raw[i] if i < len(raw) else ""
        rows.append(row)
    return headers, rows


def _num(value) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


INTERVIEW_DEFAULTS = {
    "", "sem retorno", "não", "nao", "no", "n/a", "na", "-", "default",
    "descartado", "rejeitado", "reprovado", "aguardando", "pendente",
    "em análise", "em analise", "nenhuma", "não fiz", "nao fiz",
}


def is_interview_value(value) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text not in INTERVIEW_DEFAULTS


def summarize(tab: str, rows: list[dict]) -> dict:
    total = len(rows)
    out: dict = {"total": total}

    def count_true(field: str) -> int:
        return sum(1 for r in rows if str(r.get(field, "")).strip().lower() in {"sim", "yes", "true", "1"})

    scores = [s for s in (_num(r.get("Score")) for r in rows) if s is not None]
    out["avg_score"] = round(sum(scores) / len(scores), 1) if scores else None
    out["remote"] = count_true("is_remote")

    if tab == "jobs":
        out["applied"] = count_true("Apply")
        out["interview"] = sum(1 for r in rows if is_interview_value(r.get("Entrevista")))
        buckets = [0, 0, 0, 0, 0]
        for s in scores:
            idx = min(4, max(0, int(s // 20)))
            buckets[idx] += 1
        out["score_buckets"] = buckets
    elif tab == "rejected":
        out["applied"] = count_true("Apply")
        out["visa_block"] = count_true("Visto?")

    return out


def read_tab(tab: str, *, force: bool = False) -> dict:
    """Return ``{ok, tab, headers, rows, summary, updated_at, error}``."""
    settings = _load_settings()
    key = "jobs" if tab not in _TAB_KEYS else tab
    name = settings.get(_TAB_KEYS.get(key, "jobs_tab"), "Jobs")
    ttl = int(settings.get("cache_ttl", DEFAULT_CACHE_TTL))
    now = time.time()

    cached = _cache.get(key)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]

    sid = settings.get("spreadsheet_id")
    sa = settings.get("service_account")
    if not sid or not sa or not Path(sa).is_file():
        return {
            "ok": False,
            "tab": key,
            "error": "Sheets não configurado (spreadsheet_id/service_account ausente).",
            "headers": [],
            "rows": [],
            "summary": {},
        }

    try:
        import requests

        token = _get_token(sa)
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{name}"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        values = resp.json().get("values", [])
        headers, rows = _rows_to_dicts(values)
        result = {
            "ok": True,
            "tab": key,
            "tab_name": name,
            "headers": headers,
            "rows": rows,
            "summary": summarize(key, rows),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "error": None,
        }
        _cache[key] = (now, result)
        return result
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "tab": key,
            "tab_name": name,
            "error": str(e),
            "headers": [],
            "rows": [],
            "summary": {},
        }
