"""Matching local — Fase 9.

Calcula, para cada vaga, um ``overall_score`` 0..100 agregando 7 dimensões:

* role_match        (0..100)
* skills_match      (0..100)
* cloud_match       (0..100)
* seniority_match   (0..100)
* location_match    (0..100)
* remote_match      (0..100)
* experience_match  (0..100)

Categorias:

* 90..100 EXCELLENT
* 80..89  STRONG
* 70..79  GOOD
* 60..69  POSSIBLE
* < 60    LOW

Nada é descartado por baixo de 60 — o threshold é responsabilidade do
config (veja ``InhireConfig.min_score``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterable

from careerops.collector.inhire.cv_profile import CVProfile
from careerops.collector.inhire.normalize import normalize_seniority


CATEGORY_BANDS = (
    (90, "EXCELLENT"),
    (80, "STRONG"),
    (70, "GOOD"),
    (60, "POSSIBLE"),
    ( 0, "LOW"),
)


@dataclass
class MatchScore:
    role_match: float
    skills_match: float
    cloud_match: float
    seniority_match: float
    location_match: float
    remote_match: float
    experience_match: float
    overall_score: float
    category: str

    def as_dict(self) -> dict:
        return asdict(self)


def _category(score: float) -> str:
    for threshold, label in CATEGORY_BANDS:
        if score >= threshold:
            return label
    return "LOW"


# ----------------------------------------------------------------- helpers


def _has(term: str, blob: str) -> bool:
    return bool(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", blob))


def _match_keywords(needles: Iterable[str], blob: str) -> tuple[int, list[str]]:
    """Retorna (qtd_matches, lista_de_matches). Match = substring com boundary."""
    hits: list[str] = []
    for n in needles:
        if _has(n.lower(), blob.lower()):
            hits.append(n)
    return len(hits), hits


# --------------------------------------------------------------- dimensions


def role_score(title: str, profile: CVProfile) -> float:
    """100 se bate cargo prioritário; 0 se nada bate."""
    t = (title or "").lower()
    if not t:
        return 0.0
    for role in profile.roles:
        if _has(role, t):
            # Compensa ordem (primeiro cargo é mais forte)
            idx = profile.roles.index(role)
            return max(60.0, 100.0 - idx * 3.5)
    return 0.0


def skills_score(text: str, profile: CVProfile) -> float:
    """Soma pesos das skills que aparecem; normalizada por max_skill_score."""
    blob = (text or "").lower()
    if not blob:
        return 0.0
    total = sum(w for kw, w in profile.skills.items() if _has(kw.lower(), blob))
    if total == 0:
        return 0.0
    return round(100.0 * min(total, profile.max_skill_score) / profile.max_skill_score, 1)


def cloud_score(text: str) -> float:
    blob = (text or "").lower()
    if not blob:
        return 0.0
    clouds = ["oci", "aws", "azure", "gcp"]
    matched = sum(1 for c in clouds if _has(c, blob))
    if matched == 0:
        return 0.0
    # Penaliza vagas "multi-cloud genérico" e privilegia AWS/OCI
    bias = 0.0
    if _has("oci", blob):
        bias += 30.0
    if _has("aws", blob):
        bias += 20.0
    return min(100.0, 50.0 * matched + bias)


def seniority_score(value: str, title: str, profile: CVProfile) -> float:
    level = normalize_seniority(value, title)
    if not level:
        return 50.0        # sem info: neutro
    return 100.0 if level in profile.seniority_accepted else 30.0


def location_score(location: str, profile: CVProfile, *, remote_ok: bool) -> float:
    loc = (location or "").lower()
    if not loc:
        return 60.0
    if any(c in loc for c in profile.countries) or "remote" in loc or "anywhere" in loc:
        return 100.0
    # BR sem cidade específica
    if "brasil" in loc or "brazil" in loc:
        return 90.0
    return 20.0


def remote_score(workplace: str, is_remote: bool | None, profile: CVProfile) -> float:
    if is_remote is True or (workplace or "").lower() in {"remote", "remoto", "remota"}:
        return 100.0 if profile.prefers_remote else 70.0
    if workplace == "hybrid":
        return 60.0 if profile.prefers_remote else 50.0
    if workplace == "onsite":
        return 10.0 if profile.prefers_remote else 30.0
    return 50.0


def experience_score(text: str, profile: CVProfile) -> float:
    """Procura anos de experiência no texto (regex simples)."""
    blob = (text or "").lower()
    matches = re.findall(r"(\d{1,2})\s*\+?\s*(?:years|anos|yrs)\b", blob)
    if not matches:
        return 60.0
    years = max(int(m) for m in matches)
    if years >= profile.min_years_experience:
        return 100.0
    ratio = years / max(1, profile.min_years_experience)
    return round(50.0 + 50.0 * ratio, 1)


# ------------------------------------------------------------------- main


def compute_match(
    *,
    title: str,
    description: str,
    workplace_type: str = "",
    is_remote: bool | None = None,
    location: str = "",
    seniority: str = "",
    profile: CVProfile | None = None,
) -> MatchScore:
    profile = profile or CVProfile()
    blob = f"{title} {description} {workplace_type} {location} {seniority}"

    role   = role_score(title, profile)
    skills = skills_score(blob, profile)
    cloud  = cloud_score(blob)
    senior = seniority_score(seniority, title, profile)
    loc    = location_score(location, profile, remote_ok=profile.prefers_remote)
    remote = remote_score(workplace_type, is_remote, profile)
    exp    = experience_score(description or title, profile)

    # Pesos: skills e role dominam, demais completam
    weights = {
        "role":  0.30,
        "skills": 0.25,
        "cloud":   0.10,
        "senior":  0.10,
        "loc":     0.10,
        "remote":  0.10,
        "exp":     0.05,
    }
    overall = (
        weights["role"]   * role
        + weights["skills"] * skills
        + weights["cloud"]  * cloud
        + weights["senior"] * senior
        + weights["loc"]    * loc
        + weights["remote"] * remote
        + weights["exp"]    * exp
    )
    overall = round(min(100.0, max(0.0, overall)), 1)
    return MatchScore(
        role_match=round(role, 1),
        skills_match=skills,
        cloud_match=round(cloud, 1),
        seniority_match=round(senior, 1),
        location_match=round(loc, 1),
        remote_match=round(remote, 1),
        experience_match=exp,
        overall_score=overall,
        category=_category(overall),
    )
