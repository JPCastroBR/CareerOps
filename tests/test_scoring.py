"""Unit tests for the deterministic profile scorer (Fase 9.3).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops.collector.scoring import build_scorer  # noqa: E402
from careerops.profile import load_profile  # noqa: E402

SCORER = build_scorer(load_profile(os.path.join(ROOT, "profile.yaml")))


def row(title, description="", location="", is_remote="True"):
    return {"title": title, "description": description, "location": location,
            "is_remote": is_remote}


class RelevantRolesTests(unittest.TestCase):
    def test_oci_infra_strong(self):
        r = SCORER.score(row(
            "Consultor de Infraestrutura – Oracle Cloud (OCI) SR",
            "OCI, Linux, Docker, Ansible, VPN, firewall, Zabbix, Grafana, PowerShell, migração lift-and-shift.",
            "Remoto, BR"))
        self.assertGreaterEqual(r.score, 80, r.rationale)
        self.assertFalse(r.blockers)
        self.assertIn("oci", r.matched.get("hands_on", []))

    def test_senior_devops_with_learning_stack_not_rejected(self):
        # AWS/Terraform/Kubernetes are learning -> must NOT drop below threshold alone.
        r = SCORER.score(row(
            "Senior DevOps Engineer (AWS) - Remote - Latin America",
            "AWS, Terraform, Kubernetes, CI/CD improvements.",
            ""))
        self.assertGreaterEqual(r.score, 71, r.rationale)

    def test_target_beats_ambiguous(self):
        good = SCORER.score(row("Site Reliability Engineer", "Linux, Docker", "Remoto, BR"))
        amb = SCORER.score(row("Specialist Engineer", "Linux, Docker", "Remoto, BR"))
        self.assertGreater(good.score, amb.score)


class OffProfileTests(unittest.TestCase):
    def test_developer_role_low(self):
        r = SCORER.score(row("Desenvolvedor Full Stack Sênior", "React, Node, AWS, Docker"))
        self.assertLess(r.score, 71)
        self.assertIn("func=off", r.rationale)

    def test_data_engineer_low(self):
        r = SCORER.score(row("Senior Data Engineer", "PySpark, Airflow, AWS"))
        self.assertLess(r.score, 71)


class AmbiguousTests(unittest.TestCase):
    def test_unknown_title_not_auto_approved(self):
        r = SCORER.score(row("Specialist Engineer", "cloud infrastructure linux"))
        self.assertLess(r.score, 71)

    def test_empty_row_is_low(self):
        r = SCORER.score(row("", "", ""))
        self.assertLess(r.score, 71)


class BlockerTests(unittest.TestCase):
    def test_visa_blocker(self):
        r = SCORER.score(row("DevOps Engineer", "No visa sponsorship. AWS, Linux.", "Remoto, BR"))
        self.assertEqual(r.score, 0)
        self.assertTrue(r.blockers)

    def test_language_blocker(self):
        r = SCORER.score(row("Site Reliability Engineer", "Fluent in German required. Linux.", "Berlin"))
        self.assertEqual(r.score, 0)
        self.assertTrue(r.blockers)


class SeniorityAndModalityTests(unittest.TestCase):
    def test_seniority_ordering(self):
        senior = SCORER.score(row("Senior DevOps Engineer", "Linux, Docker", "Remoto, BR"))
        junior = SCORER.score(row("Junior DevOps Engineer", "Linux, Docker", "Remoto, BR"))
        self.assertGreater(senior.score, junior.score)

    def test_onsite_br_is_low_modality(self):
        remote = SCORER.score(row("SRE", "Linux", "Remoto, BR", is_remote="True"))
        onsite = SCORER.score(row("SRE", "Linux", "São Paulo, BR", is_remote="False"))
        self.assertGreater(remote.score, onsite.score)
        self.assertIn("mod=onsite_br", onsite.rationale)


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_score(self):
        r = row("DevOps Engineer", "AWS, Terraform, Kubernetes", "Remoto, BR")
        self.assertEqual(SCORER.score(r).score, SCORER.score(r).score)


if __name__ == "__main__":
    unittest.main()
