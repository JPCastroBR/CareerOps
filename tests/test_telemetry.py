"""Tests for the runtime telemetry used by the dashboard."""
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops import telemetry as tel  # noqa: E402


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        tel.TELEMETRY_DIR = base
        tel.STATE_FILE = base / "state.json"
        tel.EVENTS_FILE = base / "events.jsonl"
        tel.JOBS_CSV = base / "jobs_analyzed.csv"
        tel._state.update({
            "run_id": None, "kind": "idle", "status": "idle",
            "started_at": None, "finished_at": None, "meta": {},
            "current_source": None, "sources": {},
            "counters": {"seen": 0, "sent": 0, "filtered": 0},
            "recent_seen": [], "last_error": None,
        })

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_lifecycle(self):
        rid = tel.start_run("scan", source="all")
        self.assertTrue(rid)
        state = tel.read_state()
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["kind"], "scan")

        tel.finish("finished", sent=2)
        state = tel.read_state()
        self.assertEqual(state["status"], "finished")
        self.assertIsNotNone(state["finished_at"])

    def test_sources_and_seen(self):
        tel.start_run("scan")
        tel.source_start("inhire")
        tel.job_seen({"company": "ACME", "title": "SRE", "location": "BR", "job_url": "u1"}, "inhire")
        tel.source_end("inhire", 1)
        state = tel.read_state()
        self.assertEqual(state["counters"]["seen"], 1)
        self.assertEqual(state["sources"]["inhire"]["found"], 1)
        self.assertEqual(state["recent_seen"][0]["company"], "ACME")

    def test_job_analyzed_writes_csv(self):
        tel.start_run("scan")
        tel.job_analyzed(
            {"company": "ACME", "title": "SRE", "location": "BR",
             "source": "inhire", "is_remote": True, "job_url": "https://x/1",
             "date_posted": "2026-10-01"},
            det_score=88,
            status="sent",
        )
        with tel.JOBS_CSV.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "ACME")
        self.assertEqual(rows[0]["url"], "https://x/1")
        self.assertEqual(rows[0]["det_score"], "88")
        self.assertTrue(rows[0]["analyzed_at"])
        self.assertEqual(tel.read_state()["counters"]["sent"], 1)

    def test_filtered_counter(self):
        tel.start_run("scan")
        tel.filtered("brazil_onsite", 3)
        self.assertEqual(tel.read_state()["counters"]["filtered"], 3)

    def test_events_capped_read(self):
        tel.start_run("scan")
        for i in range(5):
            tel.event("custom", i=i)
        events = tel.read_events(3)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1]["i"], 4)


if __name__ == "__main__":
    unittest.main()
