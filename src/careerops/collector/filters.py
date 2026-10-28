"""Reusable, dependency-light job filters.

Kept free of pandas/jobspy imports so the logic can be unit-tested in
isolation (see ``tests/test_filters.py``).
"""
from __future__ import annotations

import re

# A location is considered Brazilian when it contains "Brazil"/"Brasil" or the
# country code "BR" as a standalone token. Indeed renders locations as
# "<city>, <state>, BR", which the old `brasil|brazil` check missed entirely.
BR_LOCATION_RE = re.compile(r"brasil|brazil|\bbr\b", re.IGNORECASE)

# Words that reveal the work arrangement in title/description/location.
# Hybrid is tracked separately from on-site because the profile accepts
# remote and hybrid in Brazil but rejects on-site.
HYBRID_MARKERS_RE = re.compile(r"híbrido|hibrido|hybrid", re.IGNORECASE)
ONSITE_MARKERS_RE = re.compile(r"presencial|onsite|on-site|on site", re.IGNORECASE)

_TRUE_VALUES = {"true", "1", "yes", "sim", "remote", "remoto"}


def to_bool(value) -> bool:
    """Best-effort boolean parsing.

    Handles real booleans as well as the string forms produced later by
    ``DataFrame.astype(str)`` ("True"/"False"), where ``bool("False")`` would
    be wrongly truthy.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE_VALUES


def is_brazil_onsite(row) -> bool:
    """Return True for a Brazilian on-site job (must be discarded).

    The profile accepts **remote and hybrid** work in Brazil but rejects
    on-site. A row is discarded when its location looks Brazilian AND it is
    explicitly on-site, or when it is not remote and shows no hybrid marker.
    ``row`` only needs a mapping interface (dict / pandas Series).
    """
    location = str(row.get("location", "") or "")
    if not BR_LOCATION_RE.search(location):
        return False

    is_remote = to_bool(row.get("is_remote", False))
    text = " ".join(
        str(row.get(c, "") or "")
        for c in ("title", "description", "job_type", "job_level", "location")
    ).lower()

    if HYBRID_MARKERS_RE.search(text):
        return False  # hybrid accepted
    if is_remote and not ONSITE_MARKERS_RE.search(text):
        return False  # remote accepted
    return True  # explicit on-site, or non-remote without hybrid marker


REMOTE_MARKERS_RE = re.compile(
    r"\bremot[oa]s?\b|\bremote\b|home\s*office|teletrabalho|work\s*from\s*home", re.IGNORECASE
)

_MODE_ALIASES = {
    "remote": "remote", "remoto": "remote", "remota": "remote",
    "hybrid": "hybrid", "hibrido": "hybrid", "híbrido": "hybrid",
    "local": "local", "onsite": "local", "on-site": "local", "on site": "local",
    "presencial": "local",
}


def normalize_modes(modes) -> set[str]:
    """Normalize a list of work modes to ``{remote, hybrid, local}``."""
    out: set[str] = set()
    for mode in modes or []:
        key = str(mode or "").strip().lower()
        target = _MODE_ALIASES.get(key)
        if target:
            out.add(target)
    return out


def classify_modality(row) -> str:
    """Best-effort modality: ``remote`` | ``hybrid`` | ``onsite`` | ``unknown``."""
    text = " ".join(
        str(row.get(c, "") or "")
        for c in ("title", "description", "job_type", "job_level", "location")
    ).lower()
    if HYBRID_MARKERS_RE.search(text):
        return "hybrid"
    if ONSITE_MARKERS_RE.search(text):
        return "onsite"
    if to_bool(row.get("is_remote", False)) or REMOTE_MARKERS_RE.search(text):
        return "remote"
    return "unknown"


def modality_allowed(row, modes) -> bool:
    """True when the row's modality is enabled (unknown is preserved)."""
    allowed = normalize_modes(modes)
    if not allowed or len(allowed) == 3:
        return True
    modality = classify_modality(row)
    if modality == "unknown":
        return True
    return modality in allowed
