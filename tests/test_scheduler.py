"""Tests for the in-container scheduler (slots + due detection)."""
import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops import scheduler as sched  # noqa: E402
from careerops.schedule import get_schedule  # noqa: E402

UTC = ZoneInfo("UTC")


def make_schedule(times, digest_at="", grace=120):
    return get_schedule({"schedule": {"timezone": "UTC", "times": times,
                                      "digest_at": digest_at,
                                      "misfire_grace_minutes": grace}})


class SlotsTests(unittest.TestCase):
    def test_slots_union_times_and_digest(self):
        s = make_schedule(["06:00", "12:00"], digest_at="21:00")
        out = sched.slots(s)
        self.assertEqual(set(out), {"06:00", "12:00", "21:00"})
        self.assertTrue(out["21:00"])
        self.assertFalse(out["06:00"])


class DueTests(unittest.TestCase):
    def test_due_within_grace(self):
        s = make_schedule(["06:00"], grace=120)
        now = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
        due = sched.due_slots(s, now=now, state={"runs": {}})
        self.assertEqual([d["key"] for d in due], ["2026-10-28 06:00"])

    def test_not_due_outside_grace(self):
        s = make_schedule(["06:00"], grace=120)
        now = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
        self.assertEqual(sched.due_slots(s, now=now, state={"runs": {}}), [])

    def test_not_due_if_already_run(self):
        s = make_schedule(["06:00"], grace=120)
        now = datetime(2026, 10, 28, 6, 5, tzinfo=UTC)
        state = {"runs": {"2026-10-28 06:00": {"status": "ok"}}}
        self.assertEqual(sched.due_slots(s, now=now, state=state), [])

    def test_not_due_in_future(self):
        s = make_schedule(["06:00"], grace=120)
        now = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
        self.assertEqual(sched.due_slots(s, now=now, state={"runs": {}}), [])


if __name__ == "__main__":
    unittest.main()
