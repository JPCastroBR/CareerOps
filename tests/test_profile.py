"""Unit tests for the profile loader (``careerops.profile``).

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from careerops.profile import (  # noqa: E402
    ALLOWED_LEVELS,
    ProfileNotFoundError,
    ProfileValidationError,
    build_profile_context,
    iter_skill_entries,
    load_profile,
)

REPO_PROFILE = ROOT / "profile.yaml"


def base_dict() -> dict:
    return yaml.safe_load(REPO_PROFILE.read_text(encoding="utf-8"))


def skill(profile: dict, name: str) -> dict:
    return next(s for s in profile["skills"] if s["name"] == name)


def dump_to(directory: str, obj: dict) -> Path:
    path = Path(directory) / "profile.yaml"
    path.write_text(
        yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


class RepositoryProfileTests(unittest.TestCase):
    def test_repository_profile_loads(self):
        profile = load_profile(REPO_PROFILE)
        self.assertIsInstance(profile, dict)
        self.assertTrue(profile["source_of_truth"])
        self.assertEqual(profile["profile_version"], 2)

    def test_key_levels(self):
        p = load_profile(REPO_PROFILE)
        groups = {g["name"] for g in p["skill_groups"]}
        self.assertGreater(len(p["skills"]), 0)
        for s in p["skills"]:
            self.assertIn(s["level"], ALLOWED_LEVELS, s.get("name"))
            self.assertIn(s["group"], groups, s.get("name"))

    def test_skills_have_group_and_groups_have_kind(self):
        p = load_profile(REPO_PROFILE)
        groups = {g["name"]: g["kind"] for g in p["skill_groups"]}
        self.assertIn("Nuvem", groups)
        self.assertIn(groups["Nuvem"], {"hard", "soft"})
        for s in p["skills"]:
            self.assertIn(s["group"], groups)

    def test_skills_have_no_source(self):
        p = load_profile(REPO_PROFILE)
        for s in p["skills"]:
            self.assertNotIn("source", s)

    def test_restrictions_are_confirmed_only(self):
        p = load_profile(REPO_PROFILE)
        restrictions = p["restrictions"]
        self.assertEqual(len(restrictions), 2)
        joined = " ".join(restrictions).lower()
        for unconfirmed in ("clearance", "alemão", "francês", "holandês", "nórdic"):
            self.assertNotIn(unconfirmed, joined)

    def test_languages_do_not_use_technical_levels(self):
        p = load_profile(REPO_PROFILE)
        languages = p["languages"]
        self.assertEqual(languages[0]["language"], "Português")
        self.assertEqual(languages[0]["declared_proficiency"], "Nativo")
        for language in languages:
            self.assertNotIn("level", language)
            self.assertIn("declared_proficiency", language)
            self.assertIn("current_situation", language)
            self.assertIn("evidence_notes", language)

    def test_all_skill_levels_are_allowed(self):
        p = load_profile(REPO_PROFILE)
        entries = list(iter_skill_entries(p))
        self.assertGreater(len(entries), 0)
        for group, name, entry in entries:
            self.assertIn(entry["level"], ALLOWED_LEVELS, f"{group}.{name}")


class LoaderErrorTests(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(ProfileNotFoundError):
            load_profile("/nonexistent/does-not-exist.yaml")

    def test_invalid_yaml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.yaml"
            path.write_text("profile_version: [unclosed\n", encoding="utf-8")
            with self.assertRaises(ProfileValidationError):
                load_profile(path)

    def test_duplicate_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.yaml"
            path.write_text("profile_version: 2\nprofile_version: 3\n", encoding="utf-8")
            with self.assertRaises(ProfileValidationError):
                load_profile(path)

    def test_missing_required_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_dict()
            del data["skills"]
            path = dump_to(tmp, data)
            with self.assertRaises(ProfileValidationError) as ctx:
                load_profile(path)
            self.assertIn("chaves obrigatórias", str(ctx.exception))

    def test_invalid_level_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_dict()
            skill(data, "oci")["level"] = "expert"
            path = dump_to(tmp, data)
            with self.assertRaises(ProfileValidationError) as ctx:
                load_profile(path)
            self.assertIn("level inválido", str(ctx.exception))

    def test_invalid_group_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_dict()
            skill(data, "oci")["group"] = "grupo_que_nao_existe"
            path = dump_to(tmp, data)
            with self.assertRaises(ProfileValidationError) as ctx:
                load_profile(path)
            self.assertIn("group inválido", str(ctx.exception))

    def test_language_with_technical_level_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_dict()
            data["languages"][0]["level"] = "hands_on"
            path = dump_to(tmp, data)
            with self.assertRaises(ProfileValidationError) as ctx:
                load_profile(path)
            self.assertIn("níveis técnicos", str(ctx.exception))

    def test_valid_mutated_profile_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = base_dict()
            skill(data, "azure")["level"] = "learning"
            path = dump_to(tmp, data)
            profile = load_profile(path)
            self.assertEqual(skill(profile, "azure")["level"], "learning")


class ProfileContextTests(unittest.TestCase):
    def setUp(self):
        self.ctx = build_profile_context(load_profile(REPO_PROFILE))

    def test_has_core_sections(self):
        for section in ("OBJETIVO:", "CARGOS-ALVO:", "SENIORIDADE:", "IDIOMAS:", "MODALIDADE/LOCAL:", "REGRAS:"):
            self.assertIn(section, self.ctx)

    def test_skills_by_level(self):
        p = load_profile(REPO_PROFILE)
        levels = {s["level"] for s in p["skills"]}
        labels = {"hands_on": "HANDS_ON", "working_knowledge": "WORKING_KNOWLEDGE", "learning": "LEARNING", "conceptual": "CONCEPTUAL"}
        for level, label in labels.items():
            if level in levels:
                self.assertIn(label, self.ctx)
        self.assertTrue(any(s["name"] in self.ctx for s in p["skills"]))

    def test_preserves_limitations(self):
        self.assertIn("LIMITAÇÕES RELEVANTES", self.ctx)
        self.assertIn("oci:", self.ctx)

    def test_modality_present(self):
        self.assertIn("remoto BR", self.ctx)
        self.assertIn("presencial BR fora do escopo", self.ctx)

    def test_deterministic_and_compact(self):
        self.assertEqual(self.ctx, build_profile_context(load_profile(REPO_PROFILE)))
        self.assertLess(len(self.ctx), 6000)

    def test_no_personal_contact_data(self):
        self.assertNotIn("linkedin", self.ctx.lower())
        self.assertNotIn("@", self.ctx)


if __name__ == "__main__":
    unittest.main()
