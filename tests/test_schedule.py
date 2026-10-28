"""Tests for the config-driven schedule renderer."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops import schedule as sch  # noqa: E402


class CronTimeTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(sch._cron_time("06:00"), (6, 0))
        self.assertEqual(sch._cron_time("23:59"), (23, 59))
        self.assertEqual(sch._cron_time(" 07:05 "), (7, 5))

    def test_invalid(self):
        for bad in ["", "25:00", "12:60", "abc", "6"]:
            with self.assertRaises(ValueError):
                sch._cron_time(bad)


class BuildArgsTests(unittest.TestCase):
    def test_default_empty(self):
        self.assertEqual(sch.build_args({"time": "06:00"}), "")

    def test_digest(self):
        self.assertEqual(sch.build_args({"time": "21:00", "digest": True}), "digest")

    def test_country_and_digest(self):
        self.assertEqual(
            sch.build_args({"time": "08:00", "country": "Brazil", "digest": True}),
            "Brazil digest",
        )

    def test_explicit_args_wins(self):
        self.assertEqual(sch.build_args({"time": "08:00", "args": "Brazil banks"}), "Brazil banks")


class RenderTests(unittest.TestCase):
    def test_render_contains_markers_and_lines(self):
        schedule = {"timezone": "America/Sao_Paulo", "jobs": [
            {"time": "06:00"},
            {"time": "21:00", "digest": True},
            {"time": "08:00", "country": "Netherlands"},
        ]}
        text = sch.render_crontab(schedule, "/tmp/project")
        self.assertIn(sch.MARKER_START, text)
        self.assertIn(sch.MARKER_END, text)
        self.assertIn("0 6 * * * /tmp/project/scripts/run_careerops.sh", text)
        self.assertIn("0 21 * * * /tmp/project/scripts/run_careerops.sh digest", text)
        self.assertIn("0 8 * * * /tmp/project/scripts/run_careerops.sh Netherlands", text)

    def test_get_schedule_default(self):
        sched = sch.get_schedule({})
        self.assertEqual(len(sched["jobs"]), len(sch.DEFAULT_JOBS))
        self.assertEqual(sched["timezone"], "America/Sao_Paulo")

    def test_get_schedule_from_config(self):
        cfg = {"schedule": {"timezone": "UTC", "jobs": [{"time": "01:00"}]}}
        sched = sch.get_schedule(cfg)
        self.assertEqual(sched["timezone"], "UTC")
        self.assertEqual(sched["jobs"], [{"time": "01:00"}])


if __name__ == "__main__":
    unittest.main()


class SimpleFormatTests(unittest.TestCase):
    def test_times_and_digest(self):
        cfg = {"schedule": {"enabled": True, "timezone": "UTC",
                             "times": ["06:00", "21:00"], "digest_at": "21:00",
                             "misfire_grace_minutes": 60}}
        s = sch.get_schedule(cfg)
        self.assertEqual(s["times"], ["06:00", "21:00"])
        self.assertEqual(s["digest_at"], "21:00")
        self.assertEqual(s["grace_minutes"], 60)
        self.assertTrue(any(j.get("digest") for j in s["jobs"]))

    def test_next_runs(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        s = sch.get_schedule({"schedule": {"timezone": "UTC", "times": ["06:00", "18:00"], "digest_at": "18:00"}})
        now = datetime(2026, 10, 28, 7, 0, tzinfo=ZoneInfo("UTC"))
        runs = sch.next_runs(s, now=now, count=3)
        self.assertEqual([r["at"][:16] for r in runs],
                         ["2026-10-28T18:00", "2026-10-29T06:00", "2026-10-29T18:00"])
        self.assertTrue(runs[0]["digest"])
        self.assertFalse(runs[1]["digest"])
