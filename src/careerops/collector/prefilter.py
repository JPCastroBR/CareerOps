"""Deterministic title pre-filter (Fase 9.1).

Decides whether a job title is worth sending to the AI, prioritizing the
*function* over isolated keywords. Rules live in ``profile.yaml`` under
``title_matching`` (single source of truth); this module only applies them.

Decision rules (target keywords have priority — "se busca SRE, libera"):

1. A match in ``target`` always keeps the job (rescues *any* off-profile hit,
   e.g. "Software Engineer (SRE)" is kept because of "sre").
2. Otherwise, a **hard** off-profile category discards (developer/full stack).
3. Otherwise, a **soft** off-profile category discards.
4. No match at all -> keep as *ambiguous* (never discard unknown titles).

Dependency-light (stdlib + ``dedup.normalize_text``) so it is unit-testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from careerops.collector.dedup import normalize_text


@dataclass(frozen=True)
class TitleDecision:
    keep: bool
    reason: str
    ambiguous: bool = False


@dataclass(frozen=True)
class Rules:
    target: tuple[tuple[str, tuple[re.Pattern, ...]], ...]
    off_profile: tuple[tuple[str, bool, tuple[re.Pattern, ...]], ...]


def _kw_pattern(keyword: str) -> re.Pattern:
    # normalize_text collapses whitespace, so "full stack" stays single-spaced.
    return re.compile(r"(?<![a-z0-9])" + re.escape(str(keyword).lower()) + r"(?![a-z0-9])")


def build_rules(profile: dict) -> Rules:
    """Compile the ``title_matching`` rules from a loaded profile."""
    matching = (profile or {}).get("title_matching") or {}

    target = []
    for entry in matching.get("target", []) or []:
        patterns = tuple(_kw_pattern(k) for k in entry.get("keywords", []) or [])
        target.append((entry.get("category", "target"), patterns))

    off_profile = []
    for entry in matching.get("off_profile", []) or []:
        patterns = tuple(_kw_pattern(k) for k in entry.get("keywords", []) or [])
        off_profile.append((entry.get("category", "off"), bool(entry.get("hard", False)), patterns))

    return Rules(target=tuple(target), off_profile=tuple(off_profile))


def classify_title(title, rules: Rules) -> TitleDecision:
    """Classify a job title into keep / discard / ambiguous."""
    text = normalize_text(title)

    if not text:
        return TitleDecision(keep=True, reason="kept_empty_title", ambiguous=True)

    if not rules or (not rules.target and not rules.off_profile):
        return TitleDecision(keep=True, reason="kept_no_rules", ambiguous=True)

    target_hits = [cat for cat, pats in rules.target if any(p.search(text) for p in pats)]

    # 1. Target function has priority: it rescues any off-profile keyword.
    if target_hits:
        return TitleDecision(keep=True, reason=f"kept_target:{target_hits[0]}")

    # 2. Hard off-profile without a target hit discards.
    for category, hard, patterns in rules.off_profile:
        if hard and any(p.search(text) for p in patterns):
            return TitleDecision(keep=False, reason=f"off_profile_title:{category}")

    # 3. Soft off-profile without a target hit discards.
    for category, hard, patterns in rules.off_profile:
        if any(p.search(text) for p in patterns):
            return TitleDecision(keep=False, reason=f"off_profile_title:{category}")

    # 4. Unknown/ambiguous -> preserve.
    return TitleDecision(keep=True, reason="kept_ambiguous", ambiguous=True)


def apply_prefilter(df, rules: Rules):
    """Return ``(kept_df, stats)`` applying ``classify_title`` to ``df['title']``.

    Imported here (not at module level) so the pure helpers above stay free of
    pandas for unit tests.
    """
    from collections import Counter

    reasons: Counter = Counter()
    keep_mask: list[bool] = []
    dropped_details: list[dict] = []
    for i, title in enumerate(df.get("title", [])):
        decision = classify_title(title, rules)
        reasons[decision.reason] += 1
        keep_mask.append(decision.keep)
        if not decision.keep:
            dropped_details.append({
                "company": str(df["company"].iloc[i]) if "company" in df else "",
                "title": str(title),
                "location": str(df["location"].iloc[i]) if "location" in df else "",
                "url": str(df["job_url"].iloc[i]) if "job_url" in df else "",
                "reason": decision.reason,
            })

    kept = df[keep_mask] if keep_mask else df
    ambiguous = (
        reasons.get("kept_ambiguous", 0)
        + reasons.get("kept_empty_title", 0)
        + reasons.get("kept_no_rules", 0)
    )
    stats = {
        "total": len(df),
        "kept": int(sum(keep_mask)),
        "discarded": len(df) - int(sum(keep_mask)),
        "ambiguous": ambiguous,
        "by_reason": dict(reasons),
        "dropped_details": dropped_details,
    }
    return kept, stats
