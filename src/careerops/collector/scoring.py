"""Deterministic, profile-aware job scorer (Fase 9.3).

Produces a stable ``det_score`` (0-100) that prioritizes the **function** and
respects the profile's experience levels, so roles aligned with the target are
not rejected merely for gaps in ``learning`` technologies.

Pure (stdlib + sibling collectors); no pandas, so it is unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from careerops.collector.dedup import normalize_text
from careerops.collector.filters import (
    BR_LOCATION_RE,
    HYBRID_MARKERS_RE,
    is_brazil_onsite,
)
from careerops.collector.prefilter import build_rules, classify_title
from careerops.profile import iter_skill_entries

LEVEL_WEIGHT = {
    "hands_on": 6,
    "working_knowledge": 3,
    "learning": 1,
    "conceptual": 0,
    "none": 0,
}
FUNCTION_POINTS = {"target": 55, "ambiguous": 25, "off": 0}
SENIORITY_POINTS = {"senior": 10, "pleno": 8, "unknown": 5, "junior": 0, "lead": 3}
MODALITY_POINTS = {"remote_br": 5, "hybrid_br": 4, "intl_remote": 3, "unknown": 2, "onsite_br": 0}
SKILL_CAP = 30

# Hard blockers: regardless of fit, these are not actionable for this profile.
_BLOCKER_PATTERNS = [
    r"no visa sponsorship|no sponsorship|without sponsorship|must have right to work|valid eu work permit|eu citizen|uk citizen|must be a citizen|right to work in the uk|right to work in canada|security clearance|sc clearance|dv clearance|nato clearance|ts/sci",
    r"fluent in german|fliessend deutsch|b2 deutsch|c1 deutsch|deutschkenntnisse|fluent in french|courant francais|fluent in dutch|vloeiend nederlands|fluent in norwegian|fluent in swedish|fluent in danish",
]
BLOCKER_RES = [re.compile(p) for p in _BLOCKER_PATTERNS]


def _term_pattern(term: str) -> re.Pattern:
    # Text is already normalized (lowercase, no accents, punctuation -> space).
    return re.compile(r"(?<![a-z0-9])" + re.escape(str(term).lower()) + r"(?![a-z0-9])")


@dataclass
class ScoreResult:
    score: int
    rationale: str
    blockers: list[str] = field(default_factory=list)
    matched: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


@dataclass
class Scorer:
    rules: object
    skills: list[tuple[str, str, tuple[re.Pattern, ...]]]

    def score(self, row) -> ScoreResult:
        title = str(row.get("title", "") or "")
        description = str(row.get("description", "") or "")
        location = str(row.get("location", "") or "")
        text = normalize_text(f"{title} {description}")

        # Function (reuses the pre-filter rules from profile.yaml).
        decision = classify_title(title, self.rules)
        if decision.reason.startswith("kept_target"):
            function = "target"
        elif not decision.keep:
            function = "off"
        else:
            function = "ambiguous"

        # Hard blockers.
        blockers = [p.pattern for p in BLOCKER_RES if p.search(text)]

        # Skill match by level.
        matched: dict[str, list[str]] = {level: [] for level in LEVEL_WEIGHT}
        skill_points = 0
        for name, level, patterns in self.skills:
            if any(p.search(text) for p in patterns):
                matched.setdefault(level, []).append(name)
                skill_points += LEVEL_WEIGHT.get(level, 0)
        skill_points = min(SKILL_CAP, skill_points)

        seniority = _seniority(title)
        modality = _modality(row, location, text)

        raw = (
            FUNCTION_POINTS[function]
            + skill_points
            + SENIORITY_POINTS[seniority]
            + MODALITY_POINTS[modality]
        )
        if blockers:
            raw = 0
        score = max(0, min(100, raw))

        rationale = (
            f"func={function} skl={skill_points} sen={seniority} mod={modality}"
            + (f" blockers={blockers}" if blockers else "")
        )
        missing = sorted(matched.get("none", []))
        return ScoreResult(
            score=score,
            rationale=rationale,
            blockers=blockers,
            matched={k: sorted(v) for k, v in matched.items() if v},
            missing=missing,
        )


def _seniority(title: str) -> str:
    text = normalize_text(title)
    if re.search(r"(?<![a-z0-9])(senior|sr)(?![a-z0-9])", text):
        return "senior"
    if re.search(r"(?<![a-z0-9])(pleno|pl|mid)(?![a-z0-9])", text) or "mid level" in text:
        return "pleno"
    if re.search(r"(?<![a-z0-9])(junior|jr|estagio|intern)(?![a-z0-9])", text):
        return "junior"
    if re.search(r"(?<![a-z0-9])(lead|lider)(?![a-z0-9])", text):
        return "lead"
    return "unknown"


def _modality(row, location: str, text: str) -> str:
    if BR_LOCATION_RE.search(location):
        if is_brazil_onsite(row):
            return "onsite_br"
        if HYBRID_MARKERS_RE.search(text):
            return "hybrid_br"
        return "remote_br"
    if location.strip():
        return "intl_remote"
    return "unknown"


def build_scorer(profile: dict) -> Scorer:
    """Compile a reusable scorer from a loaded profile."""
    rules = build_rules(profile)
    aliases = profile.get("skill_aliases") or {}
    skills: list[tuple[str, str, tuple[re.Pattern, ...]]] = []
    for _group, name, entry in iter_skill_entries(profile):
        level = str(entry.get("level") or "none")
        terms = {name.replace("_", " ")}
        for alias in aliases.get(name, []) or []:
            terms.add(str(alias))
        patterns = tuple(_term_pattern(t) for t in terms if t)
        skills.append((name, level, patterns))
    return Scorer(rules=rules, skills=skills)


def score_job(row, profile: dict) -> ScoreResult:
    """Convenience: build a scorer and score a single row."""
    return build_scorer(profile).score(row)
