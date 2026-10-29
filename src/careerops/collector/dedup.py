"""Job de-duplication helpers.

Dependency-light (stdlib only) so the matching logic can be unit-tested in
isolation. Two jobs are considered the same when they share the exact
``job_url`` OR the same normalized ``(company, title, location)`` key.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_text(value) -> str:
    """Lowercase, strip accents, turn punctuation into spaces and collapse.

    ``"São Paulo, SP — BR!"`` -> ``"sao paulo sp br"``.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def job_dedup_key(row) -> str:
    """Normalized identity of a job: company + title + location.

    ``row`` only needs a mapping interface (dict / pandas Series).
    """
    company = normalize_text(row.get("company", ""))
    title = normalize_text(row.get("title", ""))
    location = normalize_text(row.get("location", ""))
    return f"{company}|{title}|{location}"


def dedupe_jobs(rows: list) -> list:
    """Return ``rows`` without duplicates, keeping the first occurrence.

    A row is dropped when its exact ``job_url`` was already seen or when its
    normalized ``(company, title, location)`` key was already seen. Empty URLs
    are ignored for the URL check (so two distinct empty-URL jobs are not
    collapsed by the URL rule), while the normalized key still applies.
    """
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    result: list = []
    for row in rows:
        url = str(row.get("job_url") or "").strip()
        if url and url in seen_urls:
            continue
        key = job_dedup_key(row)
        if key in seen_keys:
            continue
        if url:
            seen_urls.add(url)
        seen_keys.add(key)
        result.append(row)
    return result


def dedupe_jobs_dropped(rows: list) -> list:
    """Return the rows that ``dedupe_jobs`` would drop (duplicates), in order.

    Mirrors ``dedupe_jobs`` exactly (same URL / normalized-key rules) so the
    pipeline can log *which* jobs were removed as batch duplicates.
    """
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    dropped: list = []
    for row in rows:
        url = str(row.get("job_url") or "").strip()
        if url and url in seen_urls:
            dropped.append(row)
            continue
        key = job_dedup_key(row)
        if key in seen_keys:
            dropped.append(row)
            continue
        if url:
            seen_urls.add(url)
        seen_keys.add(key)
    return dropped
