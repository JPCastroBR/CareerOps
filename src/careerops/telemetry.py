"""Runtime telemetry shared with the dashboard.

Files (default ``data/telemetry/``):

- ``state.json``          — current/last run state (overwritten atomically)
- ``events.jsonl``        — append-only event stream (capped)
- ``jobs_analyzed.csv``   — one row per job sent for analysis

Dependency-light (stdlib only) so the collector can import it cheaply and the
dashboard can read the same files from another container (shared bind mount).
"""
from __future__ import annotations

import atexit
import csv
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

TELEMETRY_DIR = Path(os.environ.get("CAREEROPS_TELEMETRY_DIR", "data/telemetry"))
STATE_FILE = TELEMETRY_DIR / "state.json"
EVENTS_FILE = TELEMETRY_DIR / "events.jsonl"
JOBS_CSV = TELEMETRY_DIR / "jobs_analyzed.csv"
SEEN_FILE = TELEMETRY_DIR / "seen.jsonl"
SENT_FILE = TELEMETRY_DIR / "sent_jobs.jsonl"
DISCARDED_FILE = TELEMETRY_DIR / "discarded.jsonl"

MAX_EVENTS_BYTES = 3_000_000
MAX_DISCARDED_BYTES = 2_000_000
MAX_RECENT_SEEN = 150
MAX_RECENT_EVENTS = 300

CSV_FIELDS = [
    "analyzed_at", "run_id", "company", "title", "location", "source",
    "is_remote", "det_score", "status", "url", "date_posted",
]

_lock = threading.Lock()
_run_id: str | None = None
_state: dict = {
    "run_id": None,
    "kind": "idle",
    "status": "idle",
    "started_at": None,
    "updated_at": None,
    "finished_at": None,
    "meta": {},
    "current_source": None,
    "sources": {},
    "counters": {"seen": 0, "sent": 0, "filtered": 0},
    "recent_seen": [],
    "last_error": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _g(row, key: str, default=""):
    """Read a field from a dict or pandas Series."""
    if row is None:
        return default
    try:
        value = row.get(key, default)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        value = default
    if value is None:
        return default
    return value


def _ensure_dir() -> None:
    try:
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _write_state() -> None:
    _ensure_dir()
    _state["updated_at"] = now_iso()
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError:
        try:
            STATE_FILE.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _append_event(event_kind: str, **fields) -> None:
    event = {"ts": now_iso(), "run_id": _run_id, "kind": event_kind, **fields}
    _ensure_dir()
    try:
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if EVENTS_FILE.stat().st_size > MAX_EVENTS_BYTES:
            _trim_events()
    except OSError:
        pass


def _trim_events() -> None:
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
        tail = lines[-MAX_RECENT_EVENTS * 5 :]
        EVENTS_FILE.write_text("\n".join(tail) + "\n", encoding="utf-8")
    except OSError:
        pass


def _append_csv(row) -> None:
    _ensure_dir()
    new_file = not JOBS_CSV.exists() or JOBS_CSV.stat().st_size == 0
    record = {
        "analyzed_at": now_iso(),
        "run_id": _run_id or "",
        "company": _g(row, "company"),
        "title": _g(row, "title"),
        "location": _g(row, "location"),
        "source": _g(row, "source") or _g(row, "site") or _g(row, "tenant_slug"),
        "is_remote": _g(row, "is_remote"),
        "det_score": _g(row, "det_score"),
        "status": _g(row, "status", "sent"),
        "url": _g(row, "job_url") or _g(row, "url"),
        "date_posted": _g(row, "date_posted"),
    }
    try:
        with JOBS_CSV.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(record)
    except OSError:
        pass


# ------------------------------------------------------------------ lifecycle


def start_run(kind: str = "scan", **meta) -> str:
    global _run_id
    with _lock:
        _run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        _state.update({
            "run_id": _run_id,
            "kind": kind,
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "meta": meta,
            "current_source": None,
            "sources": {},
            "counters": {"seen": 0, "sent": 0, "filtered": 0},
            "recent_seen": [],
            "last_error": None,
        })
        _append_event("run_start", kind=kind, **meta)
        _write_state()
    return _run_id


def set_state(**fields) -> None:
    with _lock:
        _state.update(fields)
        _write_state()


def event(kind: str, **fields) -> None:
    with _lock:
        _append_event(kind, **fields)


def source_start(name: str) -> None:
    with _lock:
        _state["current_source"] = name
        _state.setdefault("sources", {})[name] = {"started_at": now_iso(), "found": 0}
        _append_event("source_start", source=name)
        _write_state()


def source_end(name: str, found: int = 0) -> None:
    with _lock:
        info = _state.setdefault("sources", {}).setdefault(name, {})
        info.update({"finished_at": now_iso(), "found": int(found)})
        _state["current_source"] = None
        _append_event("source_end", source=name, found=int(found))
        _write_state()


def job_seen(row, source: str = "") -> None:
    with _lock:
        _state["counters"]["seen"] = _state["counters"].get("seen", 0) + 1
        seen = deque(_state.get("recent_seen") or [], maxlen=MAX_RECENT_SEEN)
        seen.appendleft({
            "ts": now_iso(),
            "company": _g(row, "company"),
            "title": _g(row, "title"),
            "location": _g(row, "location"),
            "source": _g(row, "source") or source,
            "url": _g(row, "job_url") or _g(row, "url"),
        })
        _state["recent_seen"] = list(seen)
        _write_state()
        _append_seen(row, source)


def job_analyzed(row, det_score=None, status: str = "sent") -> None:
    with _lock:
        _state["counters"]["sent"] = _state["counters"].get("sent", 0) + 1
        record = dict(row) if isinstance(row, dict) else {}
        if not record:
            record = row
        if det_score is not None:
            if isinstance(record, dict):
                record["det_score"] = det_score
        _append_csv(record if isinstance(record, dict) else _row_dict(row, det_score))
        _append_event(
            "job_analyzed",
            company=_g(row, "company"),
            title=_g(row, "title"),
            url=_g(row, "job_url") or _g(row, "url"),
            status=status,
            det_score=det_score,
        )
        _write_state()


def _row_dict(row, det_score) -> dict:
    return {
        "company": _g(row, "company"),
        "title": _g(row, "title"),
        "location": _g(row, "location"),
        "source": _g(row, "source"),
        "is_remote": _g(row, "is_remote"),
        "det_score": det_score if det_score is not None else _g(row, "det_score"),
        "status": "sent",
        "job_url": _g(row, "job_url"),
        "date_posted": _g(row, "date_posted"),
    }


def filtered(stage: str, discarded: int, **extra) -> None:
    with _lock:
        _state["counters"]["filtered"] = _state["counters"].get("filtered", 0) + int(discarded)
        _append_event("filter", stage=stage, discarded=int(discarded), **extra)
        _write_state()


def discarded_job(stage: str, reason: str, row, source: str = "") -> None:
    """Log one discarded job (with identity + reason) for the Coleta view.

    Appended to ``discarded.jsonl`` (capped), separate from the event stream so
    the pipeline can explain *which* jobs were dropped and why.
    """
    record = {
        "ts": now_iso(),
        "run_id": _run_id,
        "stage": stage,
        "reason": reason,
        "company": _g(row, "company"),
        "title": _g(row, "title"),
        "location": _g(row, "location"),
        "url": _g(row, "job_url") or _g(row, "url"),
        "source": _g(row, "source") or source,
    }
    _ensure_dir()
    try:
        with DISCARDED_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if DISCARDED_FILE.stat().st_size > MAX_DISCARDED_BYTES:
            lines = DISCARDED_FILE.read_text(encoding="utf-8").splitlines()
            DISCARDED_FILE.write_text("\n".join(lines[-4000:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def finish(status: str = "finished", **fields) -> None:
    with _lock:
        if _state.get("status") != "running":
            _state["status"] = status if status != "finished" else _state.get("status", status)
        else:
            _state["status"] = status
        _state["finished_at"] = now_iso()
        _state["current_source"] = None
        _append_event("run_end", status=status, **fields)
        _write_state()


def error(message: str) -> None:
    with _lock:
        _state["status"] = "error"
        _state["last_error"] = str(message)
        _state["finished_at"] = now_iso()
        _append_event("run_error", error=str(message))
        _write_state()


# --------------------------------------------------------------------- readers


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dict(_state)


def read_events(limit: int = 100) -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    try:
        lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def read_jobs(limit: int = 200) -> list[dict]:
    if not JOBS_CSV.exists():
        return []
    try:
        with JOBS_CSV.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    return rows[-limit:][::-1]


def record_sent(payload: dict) -> None:
    """Persist the full payload sent to n8n (for a safe forced resend)."""
    _ensure_dir()
    record = {"ts": now_iso(), "url": _g(payload, "job_url") or _g(payload, "url"), "payload": payload}
    try:
        with SENT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if SENT_FILE.stat().st_size > 6_000_000:
            lines = SENT_FILE.read_text(encoding="utf-8").splitlines()
            SENT_FILE.write_text("\n".join(lines[-3000:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_sent_payload(url: str) -> dict | None:
    if not SENT_FILE.exists():
        return None
    found = None
    try:
        for line in SENT_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("url") == url and isinstance(rec.get("payload"), dict):
                found = rec["payload"]
    except OSError:
        return None
    return found


def _append_seen(row, source: str) -> None:
    record = {
        "ts": now_iso(),
        "company": _g(row, "company"),
        "title": _g(row, "title"),
        "location": _g(row, "location"),
        "source": _g(row, "source") or _g(row, "site") or source,
        "url": _g(row, "job_url") or _g(row, "url"),
    }
    _ensure_dir()
    try:
        with SEEN_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if SEEN_FILE.stat().st_size > 4_000_000:
            lines = SEEN_FILE.read_text(encoding="utf-8").splitlines()
            SEEN_FILE.write_text("\n".join(lines[-20000:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _parse_iso(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:  # noqa: BLE001
        return None


def read_seen(days: int | None = None, limit: int | None = None) -> list[dict]:
    if not SEEN_FILE.exists():
        return []
    try:
        lines = SEEN_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    cutoff = None
    if days and int(days) > 0:
        cutoff = datetime.now().astimezone() - timedelta(days=int(days))
    latest: dict[str, dict] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ts = _parse_iso(rec.get("ts"))
        if cutoff and (ts is None or ts < cutoff):
            continue
        url = str(rec.get("url") or "")
        key = url or f"{rec.get('company')}|{rec.get('title')}|{rec.get('ts')}"
        if key not in latest or str(rec.get("ts", "")) > str(latest[key].get("ts", "")):
            latest[key] = rec
    rows = sorted(latest.values(), key=lambda r: str(r.get("ts", "")), reverse=True)
    if limit:
        rows = rows[: int(limit)]
    return rows


def read_discarded(limit: int = 200, days: int | None = None) -> list[dict]:
    if not DISCARDED_FILE.exists():
        return []
    try:
        lines = DISCARDED_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    cutoff = None
    if days and int(days) > 0:
        cutoff = datetime.now().astimezone() - timedelta(days=int(days))
    out: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if cutoff:
            ts = _parse_iso(rec.get("ts"))
            if ts is None or ts < cutoff:
                continue
        out.append(rec)
    return out[-limit:][::-1]


def clear_events() -> None:
    for f in (EVENTS_FILE, DISCARDED_FILE):
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass


def read_job_urls() -> set[str]:
    if not JOBS_CSV.exists():
        return set()
    try:
        with JOBS_CSV.open("r", encoding="utf-8", newline="") as f:
            return {str(r.get("url") or "") for r in csv.DictReader(f) if r.get("url")}
    except OSError:
        return set()


def jobs_csv_path() -> Path:
    return JOBS_CSV


def _finish_on_exit() -> None:
    if _state.get("status") == "running":
        finish("finished", reason="process_exit")


atexit.register(_finish_on_exit)
