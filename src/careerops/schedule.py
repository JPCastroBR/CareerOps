"""Single source of truth for run times (``config.json`` → ``schedule``).

Simple format (recommended)::

    "schedule": {
      "enabled": true,
      "timezone": "America/Sao_Paulo",
      "times": ["06:00", "11:00", "16:00", "21:00"],
      "digest_at": "21:00",
      "misfire_grace_minutes": 120
    }

The ``scheduler`` container reads this and runs the pipeline by itself — no
cron needed. The legacy ``jobs: [{time, …}]`` format is still accepted.

If ``schedule`` is absent, ``DEFAULT_JOBS`` reproduces the historical runs.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_CONFIG = "config.json"
MARKER_START = "# >>> CAREEROPS CRON JOBS START <<<"
MARKER_END = "# >>> CAREEROPS CRON JOBS END <<<"
DEFAULT_TZ = "America/Sao_Paulo"

DEFAULT_JOBS = [
    {"time": "06:00"},
    {"time": "11:00"},
    {"time": "16:00"},
    {"time": "21:00", "digest": True},
]


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _cron_time(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m:
        raise ValueError(f"horário inválido: {value!r} (use HH:MM)")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"horário fora do intervalo: {value!r}")
    return hour, minute


def _hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def get_schedule(config: dict) -> dict:
    """Normalize config.schedule into a stable structure.

    Returns ``{enabled, timezone, grace_minutes, digest_at, times, jobs}``.
    """
    raw = (config or {}).get("schedule") or {}
    timezone = raw.get("timezone") or DEFAULT_TZ
    enabled = bool(raw.get("enabled", True))
    grace = int(raw.get("misfire_grace_minutes", 120))
    digest_at = str(raw.get("digest_at") or "").strip()

    jobs = raw.get("jobs")
    if jobs:
        if not digest_at:
            for job in jobs:
                if job.get("digest"):
                    digest_at = str(job.get("time") or "").strip()
                    break
        times = [str(j.get("time") or "").strip() for j in jobs]
    else:
        times = [str(t).strip() for t in (raw.get("times") or [j["time"] for j in DEFAULT_JOBS])]
        jobs = []
        for t in times:
            job = {"time": t}
            if digest_at and t == digest_at:
                job["digest"] = True
            jobs.append(job)

    times = [t for t in times if t]
    return {
        "enabled": enabled,
        "timezone": timezone,
        "grace_minutes": grace,
        "digest_at": digest_at,
        "times": times,
        "jobs": jobs,
        "source_frequency": raw.get("source_frequency") or {},
    }


def build_args(job: dict) -> str:
    if job.get("args") is not None:
        return str(job["args"]).strip()
    parts: list[str] = []
    country = str(job.get("country") or "").strip()
    if country:
        parts.append(country)
    if job.get("digest"):
        parts.append("digest")
    return " ".join(parts)


def _tzinfo(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().tzinfo


def next_runs(schedule: dict, now: datetime | None = None, count: int = 5) -> list[dict]:
    """Return the next ``count`` scheduled slots (``{at, digest}``)."""
    tz = _tzinfo(schedule.get("timezone") or DEFAULT_TZ)
    now = now.astimezone(tz) if now else datetime.now(tz)
    slots: list[tuple[int, int]] = []
    for t in schedule.get("times") or []:
        try:
            slots.append(_cron_time(t))
        except ValueError:
            continue
    slots = sorted(set(slots))
    digest_at = str(schedule.get("digest_at") or "")
    out: list[datetime] = []
    for add in range(0, 4):
        day: date = now.date() + timedelta(days=add)
        for hour, minute in slots:
            dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if dt > now:
                out.append(dt)
    out = sorted(out)[:count]
    return [
        {"at": dt.isoformat(timespec="minutes"), "digest": _hhmm(dt.hour, dt.minute) == digest_at}
        for dt in out
    ]


def render_crontab(schedule: dict, project_dir: str | Path) -> str:
    project = Path(project_dir).resolve()
    wrapper = project / "scripts" / "run_careerops.sh"
    log = f"{project}/logs/careerops-$(date +\\%Y\\%m\\%d).log"
    lines = [
        MARKER_START,
        "# Auto-generated from config.json (schedule) — legacy cron.",
        "# The 'scheduler' container runs this automatically; cron is optional.",
        "",
    ]
    for job in schedule.get("jobs") or []:
        try:
            hour, minute = _cron_time(job.get("time", ""))
        except ValueError:
            continue
        args = build_args(job)
        cmd = f"{minute} {hour} * * * {wrapper}"
        if args:
            cmd += f" {args}"
        lines.append(f"{cmd} >> {log} 2>&1")
    lines.append("")
    lines.append(MARKER_END)
    return "\n".join(lines)


def _strip_managed(current: str) -> str:
    out: list[str] = []
    inside = False
    for line in (current or "").splitlines():
        if line.strip() == MARKER_START:
            inside = True
            continue
        if line.strip() == MARKER_END:
            inside = False
            continue
        if inside or "run_careerops.sh" in line:
            continue
        out.append(line)
    return "\n".join(out).rstrip()


def read_crontab() -> str:
    try:
        return subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except Exception:  # noqa: BLE001
        return ""


def install_crontab(project_dir: str | Path, config_path: str | Path = DEFAULT_CONFIG) -> str:
    schedule = get_schedule(load_config(config_path))
    block = render_crontab(schedule, project_dir)
    base = _strip_managed(read_crontab())
    new = (base + "\n\n" + block + "\n") if base else (block + "\n")
    subprocess.run(["crontab", "-"], input=new, text=True, check=True)
    return block


def remove_crontab() -> None:
    base = _strip_managed(read_crontab())
    subprocess.run(["crontab", "-"], input=(base + "\n") if base else "", text=True, check=True)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description="CareerOps schedule (from config.json).")
    ap.add_argument("action", choices=["render", "install", "remove", "list", "show", "next"], nargs="?", default="render")
    ap.add_argument("--project", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args(argv)
    schedule = get_schedule(load_config(args.config))

    if args.action in ("render", "show"):
        print(render_crontab(schedule, args.project))
        return 0
    if args.action == "next":
        for item in next_runs(schedule):
            print(item["at"], "(digest)" if item["digest"] else "")
        return 0
    if args.action == "install":
        print(install_crontab(args.project, args.config))
        return 0
    if args.action == "remove":
        remove_crontab()
        print("CareerOps cron removido.")
        return 0
    if args.action == "list":
        current = read_crontab()
        inside = False
        for line in current.splitlines():
            if line.strip() == MARKER_START:
                inside = True
            if inside:
                print(line)
            if line.strip() == MARKER_END:
                inside = False
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
