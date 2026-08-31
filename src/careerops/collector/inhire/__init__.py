"""Adapter InHire — entry-point usado por ``main.py``.

Suporta tanto ``--source inhire`` quanto o helper de baixo nível
``fetch_inhire_jobs`` (espelha ``fetch_bank_jobs`` / ``fetch_recent_jobs``).
"""
from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

from careerops.collector.inhire.config import InhireConfig, load_config
from careerops.collector.inhire.cv_profile import CVProfile
from careerops.collector.inhire.dedup import dedupe_jobs
from careerops.collector.inhire.http import InhireHttp
from careerops.collector.inhire.job_collector import collect_all_tenants
from careerops.collector.inhire.repository import JobRepository, TenantRecord, TenantRepository
from careerops.collector.inhire.tenant_discovery import discover_tenants
from careerops.collector.inhire.tenant_validator import validate_tenants

log = logging.getLogger("careerops.inhire")


__all__ = [
    "InhireConfig",
    "CVProfile",
    "TenantRepository",
    "JobRepository",
    "TenantRecord",
    "fetch_inhire_jobs",
    "discover_tenants",
    "validate_tenants",
    "collect_all_tenants",
    "dedupe_jobs",
    "build_inhire_dataframe",
]


def build_inhire_dataframe(
    *,
    cfg: InhireConfig | None = None,
    tenants: Iterable[TenantRecord] | None = None,
    profile: CVProfile | None = None,
    http: InhireHttp | None = None,
    job_repo: JobRepository | None = None,
) -> pd.DataFrame:
    """Pipeline alto nível: ``validate → collect → dedupe``.

    Se ``tenants`` for ``None``, lê de :class:`TenantRepository`.
    """
    cfg = cfg or load_config()
    if not cfg.enabled:
        log.info("inhire: adapter disabled (config.inhire.enabled=false)")
        return pd.DataFrame()

    tenants_repo = TenantRepository()
    if tenants is None:
        tenants = [t for t in tenants_repo.all() if t.active]

    profile = profile or CVProfile()
    http = http or InhireHttp(cfg.http)
    job_repo = job_repo or JobRepository()

    df, _stats = collect_all_tenants(
        tenants,
        cfg=cfg,
        profile=profile,
        http=http,
        job_repo=job_repo,
    )
    if df.empty:
        return df

    deduped, _kept, _dup = dedupe_jobs(df)
    return deduped.reset_index(drop=True)


def fetch_inhire_jobs(
    banks_seeds: Iterable[str] | None = None,
    *,
    cfg: InhireConfig | None = None,
    min_score: int | None = None,
) -> pd.DataFrame:
    """Wrapper compatível com o estilo de ``fetch_bank_jobs``.

    Fluxo: ``seed + discovery → validate → collect → dedupe``.
    """
    cfg = cfg or load_config()
    if not cfg.enabled:
        log.info("inhire: adapter disabled")
        return pd.DataFrame()

    seed_slugs = list(banks_seeds or [])
    http = InhireHttp(cfg.http)
    try:
        # 1) descoberta
        discovered = discover_tenants(cfg, http=http)
        # 2) mesclar slugs passados como argumento
        for s in seed_slugs:
            discovered.setdefault(s.lower(), "arg")
        # 3) validar
        if not discovered:
            log.info("inhire: no tenants discovered")
            return pd.DataFrame()
        tenant_recs = validate_tenants(discovered.keys(), cfg, http=http)
        active = [t for t in tenant_recs if t.active]
        if not active:
            log.info("inhire: no active tenants after validation")
            return pd.DataFrame()
        # 4) coletar + dedupar
        df, _stats = collect_all_tenants(active, cfg=cfg, http=http, min_score=min_score)
        if df.empty:
            return df
        deduped, _kept, _dup = dedupe_jobs(df)
        return deduped.reset_index(drop=True)
    finally:
        http.close()
