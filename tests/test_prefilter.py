"""Unit tests for the deterministic title pre-filter (Fase 9.1).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops.collector.prefilter import build_rules, classify_title  # noqa: E402
from careerops.profile import load_profile  # noqa: E402

RULES = build_rules(load_profile(os.path.join(ROOT, "profile.yaml")))


def decision(title):
    return classify_title(title, RULES)


class KeepRelevantTitlesTests(unittest.TestCase):
    def assert_kept(self, title):
        d = decision(title)
        self.assertTrue(d.keep, f"deveria manter: {title!r} -> {d.reason}")

    def test_sre_and_reliability(self):
        self.assert_kept("Site Reliability Engineer")
        self.assert_kept("Analista de SRE Pleno")
        self.assert_kept("SRE / DevOps")

    def test_devops_and_platform(self):
        self.assert_kept("DevOps Engineer")
        self.assert_kept("Engenheiro(a) de Plataforma / SRE")
        self.assert_kept("Platform Engineer")

    def test_infrastructure_and_cloud(self):
        self.assert_kept("ENGENHEIRO DE INFRAESTRUTURA")
        self.assert_kept("Engenheiro de Infraestrutura Cloud Sênior")
        self.assert_kept("Cloud Infrastructure Engineer")
        self.assert_kept("Pessoa Engenheira Cloud Sênior")
        self.assert_kept("Cloud Operations Engineer")

    def test_observability_and_sysadmin(self):
        self.assert_kept("Analista de Observabilidade e Monitoração Sênior")
        self.assert_kept("System Administrator")
        self.assert_kept("Administrador de Sistemas Infraestrutura de Cloud")


class DiscardOffProfileTitlesTests(unittest.TestCase):
    def assert_discarded(self, title, category=None):
        d = decision(title)
        self.assertFalse(d.keep, f"deveria descartar: {title!r}")
        self.assertTrue(d.reason.startswith("off_profile_title:"), d.reason)
        if category:
            self.assertEqual(d.reason, f"off_profile_title:{category}")

    def test_software_dev_pt_en(self):
        self.assert_discarded("Desenvolvedor Full Stack Java", "dev_software")
        self.assert_discarded("Senior Full Stack Engineer", "dev_software")
        self.assert_discarded("Golang Developer", "dev_software")
        self.assert_discarded("Engenheiro de Software Sr. (Java)", "dev_software")

    def test_data_ml(self):
        self.assert_discarded("Senior Data Engineer (GCP)", "data_ml")
        self.assert_discarded("Senior Staff AI/ ML Engineer", "data_ml")

    def test_qa_product_sales_support(self):
        self.assert_discarded("Analista de Testes SR", "qa")
        self.assert_discarded("Analista de Produtos Sênior", "product_management")
        self.assert_discarded("Cloud Account Executive", "sales")
        self.assert_discarded("Analista de Suporte Técnico", "support")

    def test_management_and_architect_and_security(self):
        self.assert_discarded("Diretor(a) CTO AWS", "management")
        self.assert_discarded("Head of Engineering", "management")
        self.assert_discarded("Solutions Architect", "architect")
        self.assert_discarded("AWS Cloud Architect", "architect")
        self.assert_discarded("Cloud Security Analyst", "security")
        self.assert_discarded("Analista de Segurança da Informação", "security")

    def test_erp_dba(self):
        self.assert_discarded("DBA Sênior", "erp_dba")
        self.assert_discarded("Consultor SAP MM", "erp_dba")


class AmbiguousTitlesTests(unittest.TestCase):
    def assert_ambiguous_kept(self, title):
        d = decision(title)
        self.assertTrue(d.keep, f"ambígua deveria ser preservada: {title!r}")
        self.assertEqual(d.reason, "kept_ambiguous")
        self.assertTrue(d.ambiguous)

    def test_unknown_titles_preserved(self):
        self.assert_ambiguous_kept("CPI Engineer (GCP)")
        self.assert_ambiguous_kept("Specialist Engineer")
        self.assert_ambiguous_kept("Cloud Security Engineer")  # não descartar cloud security genérico

    def test_empty_titles_preserved(self):
        for value in ("", None, "   ", "###"):
            d = decision(value)
            self.assertTrue(d.keep)
            self.assertTrue(d.ambiguous)


class ConflictRulesTests(unittest.TestCase):
    def test_target_beats_hard_off(self):
        # "desenvolvedor" + "infraestrutura" -> alvo vence, mantém (ex.: Software Engineer (SRE)).
        d = decision("Desenvolvedor de Infraestrutura e Automação Sênior")
        self.assertTrue(d.keep)
        self.assertTrue(d.reason.startswith("kept_target:"), d.reason)
        # O exemplo do usuário: "software" (dev) vs "sre" (alvo) -> libera.
        self.assertTrue(decision("Software Engineer Spec III (SRE)").keep)

    def test_target_beats_soft_off(self):
        self.assertTrue(decision("SRE Tech Lead").keep)
        self.assertTrue(decision("Infrastructure Architect").keep)

    def test_infra_security_with_target_is_kept(self):
        self.assertTrue(decision("Security Infrastructure Engineer").keep)
        self.assertTrue(decision("DevSecOps Engineer").keep)

    def test_infra_with_automation_is_not_dev(self):
        self.assertTrue(decision("Analista de Infraestrutura e Automação Sênior").keep)

    def test_off_profile_without_target_still_discards(self):
        # Sem palavra-alvo, hard off-profile continua descartando.
        d = decision("Senior Full Stack Engineer")
        self.assertFalse(d.keep)
        self.assertEqual(d.reason, "off_profile_title:dev_software")

    def test_target_rescues_even_management_word(self):
        # "cloud operations" é palavra-alvo -> resgata até "head de".
        d = decision("HEAD DE HYBRID CLOUD OPERATIONS")
        self.assertTrue(d.keep)
        self.assertTrue(d.reason.startswith("kept_target:"), d.reason)

    def test_word_boundaries_avoid_false_positives(self):
        # "qa" não deve casar em "Qatar"; "ios" não em "kiosk".
        self.assertTrue(decision("Qatar Operations Engineer").keep)
        self.assertTrue(decision("Kiosk Operations Engineer").keep)


if __name__ == "__main__":
    unittest.main()
