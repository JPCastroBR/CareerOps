"""Loader and validator for the structured CareerOps profile (``profile.yaml``).

This module is standalone: it parses and validates the profile but is NOT
wired into the pipeline yet, so it does not change the current behaviour.

Usage::

    from careerops.profile import load_profile, ProfileValidationError

    profile = load_profile("profile.yaml")
    next(s for s in profile["skills"] if s["name"] == "oci")["level"]  # "hands_on"
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

DEFAULT_PROFILE_PATH = Path("profile.yaml")

ALLOWED_LEVELS = frozenset(
    {"hands_on", "working_knowledge", "learning", "conceptual", "none"}
)
ALLOWED_KINDS = frozenset({"hard", "soft"})

REQUIRED_KEYS: tuple[str, ...] = (
    "professional_objective",
    "target_roles",
    "seniority",
    "experience",
    "skill_groups",
    "skills",
    "certifications",
    "languages",
    "work_preferences",
    "location",
    "restrictions",
    "desired_job_types",
    "undesired_job_types",
)

# Groups whose entries are {name: {level, source, evidence, limitations, ...}}.
SKILL_GROUPS: tuple[str, ...] = (
    "skills",
    "cloud",
    "infrastructure",
    "operating_systems",
    "networking_security",
    "automation",
    "containers_orchestration",
    "monitoring_observability",
    "backup",
    "databases",
)

LANGUAGE_KEYS: tuple[str, ...] = (
    "language",
    "declared_proficiency",
    "current_situation",
    "evidence_notes",
)


class ProfileError(Exception):
    """Base error for profile loading and validation."""


class ProfileNotFoundError(ProfileError):
    """Raised when the profile file does not exist."""


class ProfileValidationError(ProfileError):
    """Raised when the profile content is structurally invalid."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """``SafeLoader`` that rejects duplicate mapping keys."""


def _construct_mapping(loader: _UniqueKeyLoader, node, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ProfileValidationError(f"chave duplicada no profile: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _validate_skills(data: dict) -> None:
    """Validate skills in the new schema (or the legacy nested groups)."""
    skills = data.get("skills")

    if isinstance(skills, dict):  # legacy: {group: {name: {level, ...}}}
        for group in SKILL_GROUPS:
            entries = data.get(group)
            if not isinstance(entries, dict):
                raise ProfileValidationError(f"{group!r} deve ser um mapeamento")
            for name, entry in entries.items():
                where = f"{group}.{name}"
                if not isinstance(entry, dict):
                    raise ProfileValidationError(f"{where!r} deve ser um mapeamento")
                if entry.get("level") not in ALLOWED_LEVELS:
                    raise ProfileValidationError(
                        f"{where}.level inválido: {entry.get('level')!r}; use {sorted(ALLOWED_LEVELS)}"
                    )
        return

    groups = data.get("skill_groups")
    if not isinstance(groups, list) or not groups:
        raise ProfileValidationError("'skill_groups' deve ser uma lista não vazia")

    names: set[str] = set()
    for index, group in enumerate(groups):
        where = f"skill_groups[{index}]"
        if not isinstance(group, dict):
            raise ProfileValidationError(f"{where!r} deve ser um mapeamento")
        name = str(group.get("name") or "").strip()
        kind = group.get("kind")
        if not name:
            raise ProfileValidationError(f"{where}.name é obrigatório")
        if kind not in ALLOWED_KINDS:
            raise ProfileValidationError(
                f"{where}.kind inválido: {kind!r}; use {sorted(ALLOWED_KINDS)}"
            )
        if name in names:
            raise ProfileValidationError(f"grupo duplicado: {name!r}")
        names.add(name)

    if not isinstance(skills, list):
        raise ProfileValidationError("'skills' deve ser uma lista")

    for index, skill in enumerate(skills):
        where = f"skills[{index}]"
        if not isinstance(skill, dict):
            raise ProfileValidationError(f"{where!r} deve ser um mapeamento")
        if not str(skill.get("name") or "").strip():
            raise ProfileValidationError(f"{where}.name é obrigatório")
        if skill.get("group") not in names:
            raise ProfileValidationError(
                f"{where}.group inválido: {skill.get('group')!r}; use um grupo de {sorted(names)}"
            )
        if skill.get("level") not in ALLOWED_LEVELS:
            raise ProfileValidationError(
                f"{where}.level inválido: {skill.get('level')!r}; use {sorted(ALLOWED_LEVELS)}"
            )


def _validate_languages(data: dict) -> None:
    languages = data.get("languages")
    if not isinstance(languages, list) or not languages:
        raise ProfileValidationError("'languages' deve ser uma lista não vazia")
    for index, language in enumerate(languages):
        where = f"languages[{index}]"
        if not isinstance(language, dict):
            raise ProfileValidationError(f"{where!r} deve ser um mapeamento")
        if "level" in language:
            raise ProfileValidationError(
                f"{where} não deve usar níveis técnicos (campo 'level')"
            )
        if not language.get("language"):
            raise ProfileValidationError(f"{where} sem 'language'")
        if not (language.get("declared_proficiency") or language.get("cefr_level")):
            raise ProfileValidationError(f"{where} sem proficiência ('cefr_level' ou 'declared_proficiency')")


def validate_profile(data: Any) -> dict:
    """Validate an already-parsed profile and return it.

    Raises ``ProfileValidationError`` on any structural problem.
    """
    if not isinstance(data, dict):
        raise ProfileValidationError("o profile deve ser um mapeamento no nível raiz")

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ProfileValidationError(f"chaves obrigatórias ausentes: {missing}")

    _validate_skills(data)
    _validate_languages(data)
    return data


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> dict:
    """Load, validate and return the profile from ``path``.

    :raises ProfileNotFoundError: file does not exist.
    :raises ProfileValidationError: invalid YAML, duplicate keys or schema errors.
    """
    profile_path = Path(path)
    if not profile_path.is_file():
        raise ProfileNotFoundError(f"profile não encontrado em {profile_path!s}")

    try:
        raw = profile_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - environment dependent
        raise ProfileError(f"falha ao ler {profile_path!s}: {exc}") from exc

    try:
        data = yaml.load(raw, Loader=_UniqueKeyLoader)
    except ProfileValidationError:
        raise
    except yaml.YAMLError as exc:
        raise ProfileValidationError(f"YAML inválido em {profile_path!s}: {exc}") from exc

    return validate_profile(data)


def iter_skill_entries(profile: dict) -> Iterator[tuple[str, str, dict]]:
    """Yield ``(group, name, entry)`` for every skill entry in the profile."""
    skills = profile.get("skills")
    if isinstance(skills, list):
        for entry in skills:
            if isinstance(entry, dict):
                yield str(entry.get("group") or ""), str(entry.get("name") or ""), entry
        return
    for group in SKILL_GROUPS:
        for name, entry in (profile.get(group) or {}).items():
            yield group, name, entry


_LEVEL_LABELS = {
    "hands_on": "HANDS_ON (comprovado)",
    "working_knowledge": "WORKING_KNOWLEDGE",
    "learning": "LEARNING",
    "conceptual": "CONCEPTUAL",
}


def build_profile_context(profile: dict) -> str:
    """Build a compact, deterministic, human-readable profile context.

    Intended to be sent to the AI (per-job payload) so it can weigh function and
    experience levels instead of raw keyword overlap. Level names, evidence and
    limitations are preserved; no personal contact data is included.
    """
    parts: list[str] = ["PERFIL DO CANDIDATO (fonte: profile.yaml)"]

    objective = " ".join(str(profile.get("professional_objective") or "").split())
    if objective:
        parts.append(f"OBJETIVO: {objective}")

    target = profile.get("target_roles") or {}
    roles = list(target.get("primary") or []) + list(target.get("adjacent") or [])
    if roles:
        parts.append("CARGOS-ALVO: " + "; ".join(roles))
    if target.get("also_acceptable"):
        parts.append("TAMBÉM ACEITÁVEL: " + str(target["also_acceptable"]).strip())

    seniority = profile.get("seniority") or {}
    current = seniority.get("current") or {}
    sre = current.get("sre_observability") or {}
    bits = []
    if seniority.get("target"):
        bits.append(f"alvo {seniority['target']}")
    if current.get("it_total_years") is not None:
        bits.append(f"TI {current['it_total_years']} anos")
    if current.get("infrastructure_focus_years") is not None:
        bits.append(f"foco infra {current['infrastructure_focus_years']} anos")
    if sre.get("years") is not None:
        bits.append(f"SRE/observabilidade {sre['years']} ano(s)")
    if bits:
        parts.append("SENIORIDADE: " + ", ".join(bits) +
                     " (avaliar por evidência, não apenas pelo título)")

    experience = profile.get("experience") or []
    if experience:
        lines = ["EXPERIÊNCIA:"]
        for item in experience:
            company = str(item.get("company") or "").strip()
            period = str(item.get("period") or "").strip()
            focus = str(item.get("focus") or item.get("role") or "").strip()
            lines.append(f"- {company} ({period}): {focus}")
        parts.append("\n".join(lines))

    buckets: dict[str, list[str]] = {level: [] for level in _LEVEL_LABELS}
    limitations: list[str] = []
    for _group, name, entry in iter_skill_entries(profile):
        level = entry.get("level")
        if level in buckets:
            buckets[level].append(name)
        limitation = entry.get("limitations")
        if limitation and level in ("hands_on", "working_knowledge"):
            limitations.append(f"{name}: {limitation}")
    for level in ("hands_on", "working_knowledge", "learning", "conceptual"):
        names = sorted(set(buckets[level]))
        if names:
            parts.append(f"{_LEVEL_LABELS[level]}: " + ", ".join(names))
    if limitations:
        parts.append("LIMITAÇÕES RELEVANTES:\n" + "\n".join(f"- {x}" for x in sorted(limitations)))

    languages = profile.get("languages") or []
    if languages:
        parts.append("IDIOMAS: " + "; ".join(
            f"{l.get('language')} ({l.get('declared_proficiency')})" for l in languages
        ))

    location = profile.get("location") or {}
    prefs = profile.get("work_preferences") or {}
    pref_lines = []
    if prefs.get("remote_brazil"):
        pref_lines.append("remoto BR aceito/desejado")
    if prefs.get("hybrid_brazil"):
        pref_lines.append("híbrido BR aceito")
    if (prefs.get("onsite_brazil") or {}).get("acceptable") is False:
        pref_lines.append("presencial BR fora do escopo")
    if prefs.get("international_remote_hire_br"):
        pref_lines.append("remoto internacional contratando no BR aceito")
    relocation = prefs.get("relocation") or {}
    if relocation:
        pref_lines.append("relocação " + str(relocation.get("condition") or "aceita").strip())
    base = ", ".join(b for b in (location.get("city"), location.get("state"), location.get("country")) if b)
    if pref_lines or base:
        parts.append("MODALIDADE/LOCAL: " + "; ".join(pref_lines) + (f" | base: {base}" if base else ""))

    parts.append(
        "REGRAS: função/responsabilidades da vaga > palavras-chave; "
        "lacuna em learning NÃO reprova automaticamente; separar obrigatório de desejável; "
        "nunca elevar nível (certificação/estudo ≠ experiência profissional)."
    )
    return "\n".join(parts)
