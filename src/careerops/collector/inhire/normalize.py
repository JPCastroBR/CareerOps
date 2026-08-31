"""Normalização — Fase 7.

Converte os termos heterogêneos das APIs em rótulos canônicos usados por
matching e deduplicação.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


# ---------------------------------------------------------------- workplace

_WORKPLACE_MAP = {
    # canonical -> [variants]
    "remote":  ["remote", "remoto", "remota", "100% remote", "anywhere"],
    "hybrid":  ["hybrid", "híbrido", "hibrido", "partial remote"],
    "onsite":  ["onsite", "on-site", "on site", "presencial", "in-office", "office"],
}


def normalize_workplace(value: str | None, *, is_remote: bool | None = None) -> str:
    """Normaliza ``workplace_type`` para ``remote|hybrid|onsite``."""
    if isinstance(is_remote, bool) and is_remote:
        return "remote"
    v = (value or "").strip().lower()
    if not v:
        return ""
    for canonical, variants in _WORKPLACE_MAP.items():
        for variant in variants:
            if variant and variant in v:
                return canonical
    return ""


# ----------------------------------------------------------------- senior


_SENIOR_MAP = {
    "junior":   ["junior", "jr", "trainee", "estagiario", "estagiário", "intern"],
    "mid":      ["mid", "pleno", "intermediate", "mid-level"],
    "senior":   ["senior", "sr", "sênior", "lead"],
    "staff":    ["staff", "principal"],
}


def normalize_seniority(value: str | None, title: str = "") -> str:
    """Detecta senioridade no título ou descrição. ``""`` quando não sabe."""
    blob = f" {(value or '')} {(title or '')} ".lower()
    best = ""
    # staff > senior > mid > junior (ordem de especificidade)
    for level in ("staff", "senior", "mid", "junior"):
        for term in _SENIOR_MAP[level]:
            if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", blob):
                best = level
                break
        if best:
            return best
    return ""


# ------------------------------------------------------------------ title


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    s = re.sub(r"\s+", " ", value).strip()
    return s


def normalize_company(value: str | None) -> str:
    if not value:
        return ""
    s = value.strip()
    # Remove sufixos corporativos comuns para deduplicação
    for suffix in (" Inc.", " Inc", " LLC", " S.A.", " SA", " Ltda", " LTDA", " GmbH", " Ltd."):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip()


# ----------------------------------------------------------------- dates


def to_iso_date(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip()
    # tenta ISO
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        pass
    return raw
