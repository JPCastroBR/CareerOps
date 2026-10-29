"""Unit tests for the Brazil location / remote modality filter.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops.collector.filters import (
    to_bool, is_brazil_onsite, normalize_modes, classify_modality, modality_allowed,
    _block_pattern,
)  # noqa: E402


class ToBoolTests(unittest.TestCase):
    def test_real_bool(self):
        self.assertTrue(to_bool(True))
        self.assertFalse(to_bool(False))

    def test_string_false_is_false(self):
        # Regression: after DataFrame.astype(str), bool("False") would be True.
        self.assertFalse(to_bool("False"))
        self.assertFalse(to_bool("false"))
        self.assertFalse(to_bool("0"))
        self.assertFalse(to_bool(""))

    def test_string_true_is_true(self):
        self.assertTrue(to_bool("True"))
        self.assertTrue(to_bool("true"))
        self.assertTrue(to_bool("1"))
        self.assertTrue(to_bool("remote"))

    def test_none_is_false(self):
        self.assertFalse(to_bool(None))


class IsBrazilOnsiteTests(unittest.TestCase):
    def _row(self, **kw):
        base = {"location": "", "is_remote": False, "title": "", "description": "",
                "job_type": "", "job_level": ""}
        base.update(kw)
        return base

    def test_indeed_br_code_with_string_false_is_onsite(self):
        # The exact bug: "SP, BR" was not recognised and "False" was truthy.
        row = self._row(location="São Paulo, SP, BR", is_remote="False",
                        title="Analista de Infraestrutura")
        self.assertTrue(is_brazil_onsite(row))

    def test_brazil_word_with_false_is_onsite(self):
        row = self._row(location="Joinville, Santa Catarina, Brazil",
                        is_remote="False", title="Analista de SRE")
        self.assertTrue(is_brazil_onsite(row))

    def test_brasil_word_is_onsite(self):
        row = self._row(location="Brasil", is_remote="False")
        self.assertTrue(is_brazil_onsite(row))

    def test_remote_br_job_is_kept(self):
        row = self._row(location="Remoto, BR", is_remote="True",
                        title="DevOps Engineer")
        self.assertFalse(is_brazil_onsite(row))

    def test_brazil_hybrid_text_is_kept(self):
        row = self._row(location="São Paulo, SP, BR", is_remote="False",
                        description="Modelo de trabalho híbrido")
        self.assertFalse(is_brazil_onsite(row))

    def test_brazil_remote_flag_with_hybrid_text_is_kept(self):
        row = self._row(location="São Paulo, SP, BR", is_remote="True",
                        description="Modelo de trabalho híbrido")
        self.assertFalse(is_brazil_onsite(row))

    def test_brazil_remote_flag_but_onsite_text_is_onsite(self):
        row = self._row(location="São Paulo, Brazil", is_remote="True",
                        title="Analista de Infraestrutura (Presencial)")
        self.assertTrue(is_brazil_onsite(row))

    def test_brazil_non_remote_without_marker_is_onsite(self):
        row = self._row(location="São Paulo, SP, BR", is_remote="False",
                        title="Analista de Infraestrutura")
        self.assertTrue(is_brazil_onsite(row))

    def test_non_brazil_location_is_kept(self):
        row = self._row(location="Berlin, Germany", is_remote="False",
                        title="SRE")
        self.assertFalse(is_brazil_onsite(row))

    def test_brussels_is_not_brazil(self):
        # Guards the \bbr\b token rule against prefix false positives.
        row = self._row(location="Brussels, Belgium", is_remote="False")
        self.assertFalse(is_brazil_onsite(row))

    def test_empty_location_is_kept(self):
        row = self._row(location="", is_remote="False")
        self.assertFalse(is_brazil_onsite(row))


class WorkModesTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_modes(["remoto", "Híbrido", "presencial"]),
                         {"remote", "hybrid", "local"})
        self.assertEqual(normalize_modes(["onsite"]), {"local"})
        self.assertEqual(normalize_modes([]), set())

    def test_classify(self):
        self.assertEqual(classify_modality({"title": "SRE", "is_remote": True}), "remote")
        self.assertEqual(classify_modality({"title": "SRE", "description": "modelo híbrido"}), "hybrid")
        self.assertEqual(classify_modality({"title": "SRE", "description": "presencial em SP"}), "onsite")
        self.assertEqual(classify_modality({"title": "SRE"}), "unknown")

    def test_allowed(self):
        self.assertTrue(modality_allowed({"title": "SRE", "is_remote": True}, ["remote"]))
        self.assertFalse(modality_allowed({"title": "SRE híbrido"}, ["remote"]))
        self.assertTrue(modality_allowed({"title": "SRE híbrido"}, ["remote", "hybrid"]))
        self.assertTrue(modality_allowed({"title": "SRE"}, ["remote"]))  # unknown preserved
        self.assertTrue(modality_allowed({"title": "SRE presencial"}, ["remote", "hybrid", "local"]))

    def test_classify_prefers_structured_modality(self):
        # "presencial" no texto não deve sobrescrever workplaceType hybrid/remote.
        hybrid = {"modality": "hybrid", "description": "atuar presencial 1x a cada 15 dias"}
        self.assertEqual(classify_modality(hybrid), "hybrid")
        remote = {"modality": "remote", "description": "vaga remota... conversa presencial"}
        self.assertEqual(classify_modality(remote), "remote")
        onsite = {"modality": "onsite", "description": "modelo remoto"}
        self.assertEqual(classify_modality(onsite), "onsite")

    def test_classify_falls_back_to_text(self):
        self.assertEqual(classify_modality({"title": "SRE", "description": "modelo híbrido"}), "hybrid")
        self.assertEqual(classify_modality({"title": "SRE", "description": "presencial em SP"}), "onsite")
        self.assertEqual(classify_modality({"title": "SRE", "is_remote": True}), "remote")


class BlockPatternTests(unittest.TestCase):
    def _matches(self, terms, text):
        import re
        rx = re.compile(_block_pattern(terms), re.IGNORECASE)
        return bool(rx.search(text))

    def test_word_boundary_not_substring(self):
        # "ia" must NOT match inside "Engenharia" / "Reliability".
        self.assertFalse(self._matches(["ia"], "Engenharia de Cloud"))
        self.assertFalse(self._matches(["ia"], "Site Reliability Engineer"))
        self.assertFalse(self._matches(["bi"], "Site Reliability Engineer"))
        self.assertFalse(self._matches(["ux"], "Analista de Infraestrutura Linux/Unix"))
        self.assertFalse(self._matches(["ui"], "Arquiteto de Soluções Cloud"))

    def test_standalone_acronym_still_matches(self):
        self.assertTrue(self._matches(["ia"], "Especialista em IA"))
        self.assertTrue(self._matches(["ux"], "UX Designer"))

    def test_multi_word_phrase_matches(self):
        self.assertTrue(self._matches(["head of"], "Head of Infrastructure"))

    def test_normal_term_matches(self):
        self.assertTrue(self._matches(["junior"], "Analista Junior"))
        self.assertTrue(self._matches(["manager"], "Engineering Manager"))


if __name__ == "__main__":
    unittest.main()
