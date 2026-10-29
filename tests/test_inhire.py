"""Unit tests for the InHire collector and discovery helpers.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops.collector.inhire import (  # noqa: E402
    build_row,
    classify_workplace,
    is_remote,
    parse_listing,
    slugify,
    strip_html,
)
from careerops.collector import inhire_discovery as disc  # noqa: E402


LISTING = {
    "tenantName": "SRM",
    "jobsPage": [
        {
            "jobId": "abc-123",
            "displayName": "Analista de Infraestrutura Pl",
            "workplaceType": "Hybrid",
            "location": "São Paulo, SP, BR",
            "careerPageId": "default",
            "status": "published",
        },
        {"displayName": "sem jobId"},
    ],
}

DETAIL = {
    "jobId": "abc-123",
    "displayName": "Analista de Infraestrutura Pl",
    "workplaceType": "Remote",
    "description": "<p>Vaga <b>remota</b>&nbsp;de infra.</p>",
    "contractType": ["PJ"],
    "location": "São Paulo, SP, BR",
    "publishedAt": "2026-01-01T00:00:00.000Z",
}


class InhireHelpersTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Analista de Infraestrutura Pl"), "analista-de-infraestrutura-pl")
        self.assertEqual(slugify("SRE/DevOps — Sênior"), "sredevops-senior")
        self.assertEqual(slugify(""), "vaga")

    def test_is_remote(self):
        self.assertTrue(is_remote("Remote"))
        self.assertTrue(is_remote("remoto"))
        self.assertFalse(is_remote("Hybrid"))
        self.assertFalse(is_remote("On-site"))
        self.assertFalse(is_remote(None))

    def test_classify_workplace(self):
        self.assertEqual(classify_workplace("Remote"), "remote")
        self.assertEqual(classify_workplace("Hybrid"), "hybrid")
        self.assertEqual(classify_workplace("híbrido"), "hybrid")
        self.assertEqual(classify_workplace("Presencial"), "onsite")
        self.assertEqual(classify_workplace(""), "")
        self.assertEqual(classify_workplace(None), "")


    def test_strip_html(self):
        self.assertEqual(strip_html("<p>Olá <b>mundo</b>&nbsp;!</p>"), "Olá mundo !")

    def test_parse_listing_skips_items_without_job_id(self):
        jobs = parse_listing(LISTING)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_id"], "abc-123")
        self.assertEqual(jobs[0]["title"], "Analista de Infraestrutura Pl")
        self.assertEqual(jobs[0]["workplace_type"], "Hybrid")

    def test_build_row(self):
        listing = parse_listing(LISTING)[0]
        row = build_row("SRM", "gruposrm", listing, DETAIL)
        self.assertEqual(row["company"], "SRM")
        self.assertEqual(row["title"], "Analista de Infraestrutura Pl")
        self.assertTrue(row["is_remote"])
        self.assertEqual(row["remote"], "remote")
        self.assertEqual(row["modality"], "remote")
        self.assertEqual(row["job_type"], "PJ")
        self.assertEqual(row["source"], "inhire:gruposrm")
        self.assertEqual(
            row["job_url"],
            "https://gruposrm.inhire.app/vagas/abc-123/analista-de-infraestrutura-pl",
        )
        self.assertEqual(row["description"], "Vaga remota de infra.")


class InhireDiscoveryTests(unittest.TestCase):
    def test_slug_from_host(self):
        self.assertEqual(disc.slug_from_host("avenue.inhire.app"), "avenue")
        self.assertEqual(disc.slug_from_host("AVENUE.inhire.app."), "avenue")
        self.assertIsNone(disc.slug_from_host("api.inhire.app"))
        self.assertIsNone(disc.slug_from_host("www.avenue.inhire.app"))
        self.assertIsNone(disc.slug_from_host("example.com"))

    def test_parse_crtsh(self):
        payload = [
            {"name_value": "avenue.inhire.app\nwww.avenue.inhire.app", "common_name": "avenue.inhire.app"},
            {"name_value": "api.inhire.app"},
            "not-a-dict",
        ]
        self.assertEqual(disc.parse_crtsh(payload), {"avenue"})

    def test_parse_wayback(self):
        payload = [
            ["original"],
            ["https://gruposrm.inhire.app/vagas/x"],
            ["http://agrotools.inhire.app/"],
            ["https://sso-setup.inhire.app/"],
        ]
        self.assertEqual(disc.parse_wayback(payload), {"gruposrm", "agrotools"})

    def test_parse_seed(self):
        text = "gruposrm\n# comment\n\nAvenue\nsso-setup\n"
        self.assertEqual(disc.parse_seed(text), {"gruposrm", "avenue"})

    def test_merge_slugs_filters_reserved_and_dedupes(self):
        merged = disc.merge_slugs(["avenue", "api", "gruposrm"], {"gruposrm", "status"})
        self.assertEqual(merged, ["avenue", "gruposrm"])


class BruteForceTests(unittest.TestCase):
    def test_clean_slug(self):
        self.assertEqual(disc.clean_slug("Avenue"), "avenue")
        self.assertEqual(disc.clean_slug("gruposrm.inhire.app"), "gruposrm")
        self.assertEqual(disc.clean_slug("https://avenue.inhire.app/vagas/x"), "avenue")
        self.assertIsNone(disc.clean_slug("api"))       # reserved
        self.assertIsNone(disc.clean_slug("tech"))      # false positive
        self.assertIsNone(disc.clean_slug("# comment"))
        self.assertIsNone(disc.clean_slug(""))

    def test_parse_bruteforce_dedupes_and_filters(self):
        text = "gruposrm\n# c\nAPI\navenue\navenue\n\n"
        self.assertEqual(disc.parse_bruteforce(text), ["gruposrm", "avenue"])

    def test_bruteforce_hits_only_valid_tenants(self):
        class _Resp:
            def __init__(self, code):
                self.status_code = code
            def json(self):
                return {"jobsPage": []} if self.status_code == 200 else {}

        class _Session:
            def get(self, url, headers=None, timeout=None):
                return _Resp(200 if headers.get("X-Tenant") == "avenue" else 404)

        found = disc.bruteforce_tenants(
            _Session(), ["avenue", "api", "nope"], limit=10, request_gap=0
        )
        self.assertEqual(found, ["avenue"])


class CacheTests(unittest.TestCase):
    def test_cache_roundtrip_and_stale_fallback(self):
        import tempfile

        class _FailingSession:
            def get(self, *a, **k):
                raise RuntimeError("offline")

        with tempfile.TemporaryDirectory() as td:
            path = disc._cache_path("crtsh", td)
            disc._save_cache(path, {"avenue", "gruposrm"})
            cached = disc._load_cache(path)
            self.assertIsNotNone(cached)
            self.assertEqual(set(cached[1]), {"avenue", "gruposrm"})

            # Fonte falha -> devolve o cache (mesmo obsoleto) em vez de perder.
            out = disc._cached_source(
                _FailingSession(), "http://x", "crtsh", disc.parse_crtsh, td, 3600, True
            )
            self.assertEqual(out, {"avenue", "gruposrm"})


if __name__ == "__main__":
    unittest.main()
