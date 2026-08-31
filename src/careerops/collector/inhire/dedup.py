"""Deduplicação entre fontes — Fase 10.

Antes do merge com JobSpy/Banks/Gupy/Workday, normaliza e remove vagas que
provavelmente são a mesma oferta.

Regras (na ordem):

1. ``source_id`` único (job_id+tenant) ⇒ mesma vaga.
2. URL canônica idêntica ⇒ mesma vaga.
3. Mesma ``company + título_normalizado + location`` ⇒ mesma vaga (fuzzy leve).
"""
from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

from careerops.collector.inhire.normalize import normalize_title, normalize_company, normalize_workplace
from careerops.collector.inhire.url import title_slug


def _norm_company(s: str) -> str:
    return normalize_company(s).lower().strip()


def _norm_title(s: str) -> str:
    t = (s or "").lower()
    # remove marcadores de senioridade/país para colisão
    t = re.sub(r"\b(brasil|brazil|remote|remoto|hybrid|híbrido|onsite|presencial)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fingerprint(row: pd.Series) -> str:
    """Hash heurístico para regra 3 (mesma empresa+título+local)."""
    company = _norm_company(row.get("company", ""))
    title = _norm_title(row.get("title", ""))
    location = (row.get("location", "") or "").lower().strip()
    return f"{company}::{title}::{location}"


def _url_identity(row: pd.Series) -> str:
    return (row.get("job_url", "") or "").strip().lower().rstrip("/")


def _source_id(row: pd.Series) -> str:
    sid = row.get("source_id", "")
    tenant = row.get("tenant_slug", "")
    if sid and tenant:
        return f"{tenant.lower()}::{sid}"
    if sid:
        return f"::{sid}"
    return ""


def _row_keep_info(row: pd.Series) -> None:
    """Garante colunas auxiliares se vier do InHire (não quebra outros collectors)."""
    if "tenant_slug" not in row:
        row["tenant_slug"] = ""
    if "source_id" not in row:
        row["source_id"] = ""
    if "match_score" not in row:
        row["match_score"] = ""


def dedupe_jobs(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Devolve ``(df_deduped, new_jobs_count, dup_count)``.

    ``new_jobs_count`` é o número de linhas **únicas** que restaram;
    ``dup_count`` é o total de duplicatas removidas.
    """
    if df.empty:
        return df.copy(), 0, 0

    work = df.copy()
    for col in ("source_id", "tenant_slug", "match_score"):
        if col not in work.columns:
            work[col] = ""

    # Empilha chaves de prioridade
    keys_p1 = work.apply(_source_id, axis=1)            # source_id
    keys_p2 = work.apply(_url_identity, axis=1)         # canonical url
    keys_p3 = work.apply(_fingerprint, axis=1)          # fuzzy

    seen_keys: set[str] = set()
    seen_secondary: set[str] = set()
    keep_idx: list[int] = []
    dup = 0

    for i, row in work.iterrows():
        k1 = keys_p1[i] or ""
        k2 = keys_p2[i] or ""
        k3 = keys_p3[i] or ""

        if k1 and k1 in seen_keys:
            dup += 1
            continue
        if k2 and k2 in seen_secondary:
            dup += 1
            continue

        seen_keys.add(k1)
        seen_secondary.add(k2)

        # Para fuzzy, tolera "títulos quase iguais" (mesma chave prefix)
        k3_prefix = re.sub(r"[^a-z0-9]+", "", k3)[:60]
        if k3_prefix and k3_prefix in seen_secondary:
            dup += 1
            continue
        seen_secondary.add(k3_prefix)

        keep_idx.append(i)

    deduped = work.loc[keep_idx].reset_index(drop=True)
    return deduped, len(deduped), dup
