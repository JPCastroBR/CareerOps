"""Unit tests for job de-duplication.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops.collector.dedup import (  # noqa: E402
    dedupe_jobs,
    job_dedup_key,
    normalize_text,
)


def row(title, company, location, url="", site=""):
    return {"title": title, "company": company, "location": location,
            "job_url": url, "site": site}


class NormalizeTextTests(unittest.TestCase):
    def test_lowercase_accents_punctuation_spaces(self):
        self.assertEqual(normalize_text("  São   Paulo, SP — BR! "), "sao paulo sp br")

    def test_none_and_empty(self):
        self.assertEqual(normalize_text(None), "")
        self.assertEqual(normalize_text(""), "")


class DedupTests(unittest.TestCase):
    def test_same_url_is_deduped(self):
        rows = [
            row("SRE", "Acme", "São Paulo, BR", "https://x/1", "linkedin"),
            row("SRE", "Acme", "São Paulo, BR", "https://x/1", "indeed"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 1)

    def test_same_job_across_sources_different_urls(self):
        rows = [
            row("Senior SRE / Cloud Engineer", "CI&T", "São Paulo, SP, Brazil",
                "https://www.linkedin.com/jobs/view/111", "linkedin"),
            row("Senior SRE / Cloud Engineer", "CI&T", "São Paulo, SP, Brazil",
                "https://br.indeed.com/viewjob?jk=aaa", "indeed"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 1)

    def test_case_differences(self):
        rows = [
            row("SRE", "Acme", "São Paulo, BR", "https://x/1"),
            row("sre", "ACME", "são paulo, br", "https://x/2"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 1)

    def test_accent_differences(self):
        rows = [
            row("Engenheiro de Infraestrutura", "Ação", "São Paulo, BR", "https://x/1"),
            row("engenheiro de infraestrutura", "Acao", "Sao Paulo, BR", "https://x/2"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 1)

    def test_space_and_punctuation_differences(self):
        rows = [
            row("DevOps - Engineer", "Acme, Inc.", "Sao  Paulo, BR", "https://x/1"),
            row("DevOps Engineer", "Acme Inc", "Sao Paulo BR", "https://x/2"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 1)

    def test_similar_titles_different_companies_are_kept(self):
        rows = [
            row("DevOps Engineer", "Acme", "São Paulo, BR", "https://x/1"),
            row("DevOps Engineer", "Globex", "São Paulo, BR", "https://x/2"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 2)

    def test_same_company_and_title_different_location_are_kept(self):
        rows = [
            row("Analista de SRE", "TOTVS", "Joinville, SC, BR", "https://x/1"),
            row("Analista de SRE", "TOTVS", "São Paulo, SP, BR", "https://x/2"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 2)

    def test_same_company_and_location_different_title_are_kept(self):
        rows = [
            row("Analista de SRE", "TOTVS", "São Paulo, BR", "https://x/1"),
            row("Analista de Infraestrutura", "TOTVS", "São Paulo, BR", "https://x/2"),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 2)

    def test_empty_urls_with_different_keys_are_kept(self):
        rows = [
            row("SRE", "Acme", "São Paulo, BR", ""),
            row("Cloud Engineer", "Acme", "São Paulo, BR", ""),
        ]
        self.assertEqual(len(dedupe_jobs(rows)), 2)

    def test_keeps_first_occurrence(self):
        first = row("SRE", "Acme", "São Paulo, BR", "https://x/1", "linkedin")
        second = row("sre", "ACME", "sao paulo, br", "https://x/2", "indeed")
        result = dedupe_jobs([first, second])
        self.assertIs(result[0], first)

    def test_key_is_stable_for_equivalent_rows(self):
        a = row("Engenheiro(a) de Plataforma / SRE", "CI&T", "São Paulo, Brazil")
        b = row("Engenheiro(a) de Plataforma / SRE", "ci&t", "sao paulo brazil")
        self.assertEqual(job_dedup_key(a), job_dedup_key(b))


if __name__ == "__main__":
    unittest.main()
