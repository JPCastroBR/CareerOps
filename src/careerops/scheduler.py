"""In-container scheduler — replaces the OS cron.

Reads ``config.json → schedule`` and runs the pipeline at the configured times
(``times`` + optional ``digest_at``). State is kept in
``data/telemetry/scheduler_state.json`` so a slot is not run twice and missed
slots are caught up within ``misfire_grace_minutes``.

Run as a container: ``uv run python -m careerops.scheduler``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from careerops.schedule import _cron_time, _tzinfo, get_schedule, load_config

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("CAREEROPS_CONFIG") or BASE_DIR / "config.json")
STATE_PATH = Path(os.environ.get("CAREEROPS_SCHEDULER_STATE") or BASE_DIR / "data/telemetry/scheduler_state.json")
LOG_DIR = BASE_DIR / "logs"
POLL_SECONDS = int(os.environ.get("SCHEDULER_POLL_SECONDS", "20"))


def slots(schedule: dict) -> dict[str, bool]:
    """Return ``{"HH:MM": is_digest}`` for every configured slot."""
    out: dict[str, bool] = {}
    for t in schedule.get("times") or []:
        try:
            h, m = _cron_time(t)
        except ValueError:
            continue
        out[f"{h:02d}:{m:02d}"] = False
    digest_at = str(schedule.get("digest_at") or "").strip()
    if digest_at:
        try:
            h, m = _cron_time(digest_at)
        except ValueError:
            h = m = None  # type: ignore[assignment]
        if h is not None:
            out[f"{h:02d}:{m:02d}"] = True
    return out


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"runs": {}}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def _prune(state: dict, keep_days: int = 7) -> dict:
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    runs = {k: v for k, v in (state.get("runs") or {}).items() if k[:10] >= cutoff}
    state["runs"] = runs
    daily = {k: v for k, v in (state.get("daily") or {}).items() if k >= cutoff}
    state["daily"] = daily
    return state


def source_plan(config: dict) -> dict:
    """Return ``{"every": [...], "daily": [...], "configured": bool}``.

    ``configured`` is False when ``schedule.source_frequency`` is absent, so the
    scheduler keeps the legacy behavior (no ``--sources`` → main.py uses
    ``config.scan_sources``). With a frequency map, ``every`` sources run at
    every slot and ``daily`` sources run once per day (first slot that fires).
    """
    scan = [s for s in (config.get("scan_sources") or [])]
    if not scan:
        scan = ["linkedin", "indeed", "gupy", "workday", "inhire"]
    freq = ((config.get("schedule") or {}).get("source_frequency") or {})
    if not freq:
        return {"every": scan, "daily": [], "configured": False}
    every = [s for s in scan if str(freq.get(s, "every")).strip().lower() != "daily"]
    daily = [s for s in scan if str(freq.get(s, "")).strip().lower() == "daily"]
    return {"every": every, "daily": daily, "configured": True}


def pending_sources(plan: dict, daily_done: set[str]) -> list[str]:
    """Sources to run this slot: every + daily sources not yet run today."""
    if not plan.get("configured"):
        return []
    return plan["every"] + [s for s in plan["daily"] if s not in daily_done]


def due_slots(schedule: dict, now: datetime | None = None, state: dict | None = None) -> list[dict]:
    """Slots that are due (within grace) and not yet run today."""
    tz = _tzinfo(schedule.get("timezone") or "America/Sao_Paulo")
    now = now.astimezone(tz) if now else datetime.now(tz)
    grace = int(schedule.get("grace_minutes", 120))
    state = state if state is not None else _load_state()
    due: list[dict] = []
    for hhmm, is_digest in sorted(slots(schedule).items()):
        hour, minute = _cron_time(hhmm)
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (now - slot_dt).total_seconds() / 60.0
        if delta < 0 or delta > grace:
            continue
        key = f"{slot_dt.strftime('%Y-%m-%d')} {hhmm}"
        if key in state.get("runs", {}):
            continue
        due.append({"key": key, "time": hhmm, "digest": is_digest, "slot": slot_dt})
    return due


def _log(line: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{ts}] {line}"
    print(text, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"careerops-{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def run_scan(digest: bool, sources: list[str] | None = None) -> int:
    cmd = [sys.executable, "-m", "careerops.main"]
    if digest:
        cmd.append("--digest")
    if sources:
        cmd.append("--sources")
        cmd.append(",".join(sources))
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(BASE_DIR / "src"))
    _log(f"▶ executando: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), env=env)
        _log(f"■ finalizado (exit={proc.returncode})")
        return proc.returncode
    except Exception as e:  # noqa: BLE001
        _log(f"✖ erro ao executar: {e}")
        return 1


def main() -> int:
    _log(f"scheduler iniciado (config={CONFIG_PATH}, poll={POLL_SECONDS}s)")
    last_mtime = None
    config = load_config(CONFIG_PATH)
    schedule = get_schedule(config)
    plan = source_plan(config)
    while True:
        try:
            mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
            if mtime != last_mtime:
                config = load_config(CONFIG_PATH)
                schedule = get_schedule(config)
                plan = source_plan(config)
                last_mtime = mtime
                _log(f"agenda carregada: enabled={schedule['enabled']} "
                     f"times={schedule['times']} digest_at={schedule['digest_at']} "
                     f"fontes={plan['every']} (diárias={plan['daily']})")

            if not schedule.get("enabled", True):
                time.sleep(POLL_SECONDS)
                continue

            state = _prune(_load_state())
            for item in due_slots(schedule, state=state):
                sources = None
                today = datetime.now().strftime("%Y-%m-%d")
                if plan.get("configured"):
                    daily_done = set((state.get("daily") or {}).get(today, []))
                    pending = pending_sources(plan, daily_done)
                    if not pending:
                        _log(f"⏭ slot {item['key']} sem fontes pendentes — pulado")
                        state.setdefault("runs", {})[item["key"]] = {
                            "status": "skipped",
                            "digest": item["digest"],
                            "sources": [],
                        }
                        continue
                    sources = pending
                state.setdefault("runs", {})[item["key"]] = {
                    "status": "running",
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "digest": item["digest"],
                    "sources": sources,
                }
                _save_state(state)
                _log(f"⏰ slot {item['key']} (digest={item['digest']}, fontes={sources})")
                rc = run_scan(item["digest"], sources)
                if plan.get("configured") and rc == 0:
                    d = state.setdefault("daily", {}).setdefault(today, [])
                    for s in plan["daily"]:
                        if s not in d:
                            d.append(s)
                state["runs"][item["key"]].update({
                    "status": "ok" if rc == 0 else "error",
                    "exit": rc,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                })
                _save_state(state)
            _save_state(state)
        except Exception as e:  # noqa: BLE001
            _log(f"✖ erro no loop: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
