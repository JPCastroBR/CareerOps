"""Tenant Validator — Fase 3.

Valida cada slug candidato consultando o endpoint público:

    GET https://api.inhire.app/job-posts/public/pages
    Header X-Tenant: <slug>

Resposta com lista de vagas → tenant válido.
404/410 → inválido.
Outros erros / timeout → mantém candidato (re-tenta depois).

Usa cache em memória via :class:`InhireHttp` (``use_cache=True``) e cache em
disco via :class:`TenantRepository`.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from careerops.collector.inhire.config import InhireConfig
from careerops.collector.inhire.http import InhireHttp
from careerops.collector.inhire.repository import TenantRecord, TenantRepository

log = logging.getLogger("careerops.inhire.validator")


PAGES_URL = "/job-posts/public/pages"


def _looks_active(payload) -> int:
    """Retorna quantidade aparente de vagas num payload de páginas.

    O endpoint pode retornar ``{ 'jobPosts': {...}, ... }`` com vagas embutidas
    ou uma referência indireta. Esta função é defensiva — não tenta
    interpretar estrutura, apenas detecta se há *algo* que pareça vagas.

    A contagem exata é responsabilidade de :func:`count_jobs_from_pages`.
    """
    try:
        # Heurística simples e estável:
        if not isinstance(payload, dict):
            return 0
        # Formato real da API InHire
        if isinstance(payload.get("jobsPage"), list):
            return len(payload["jobsPage"])
        # Lista direta
        if isinstance(payload.get("data"), list):
            return len(payload["data"])
        if isinstance(payload.get("jobs"), list):
            return len(payload["jobs"])
        if isinstance(payload.get("jobPosts"), list):
            return len(payload["jobPosts"])
        # Wrapper {jobPosts: { items: [...] }}
        jp = payload.get("jobPosts") or {}
        if isinstance(jp, dict) and isinstance(jp.get("items"), list):
            return len(jp["items"])
    except Exception:
        return 0
    return 0


def count_jobs_from_pages(payload) -> int:
    """Conta vagas extraindo do payload retornado pelo endpoint público."""
    return _looks_active(payload)


def validate_tenants(
    slugs: Iterable[str],
    cfg: InhireConfig,
    repo: TenantRepository | None = None,
    http: InhireHttp | None = None,
) -> list[TenantRecord]:
    """Valida uma lista de slugs e devolve os ``TenantRecord`` resultantes.

    Side-effect: atualiza (ou descarta) o registro em ``TenantRepository``.
    """
    repo = repo or TenantRepository()
    own_http = http is None
    http = http or InhireHttp(cfg.http)
    try:
        results: list[TenantRecord] = []
        for slug in slugs:
            url = f"{cfg.api_base.rstrip('/')}{PAGES_URL}"
            status, payload = http.request(
                "GET",
                url,
                headers={"X-Tenant": slug.lower()},
                accept_status=(200,),
            )
            rec = TenantRecord(
                slug=slug.lower(),
                domain=f"{slug.lower()}.inhire.app",
                discovery_source=repo.get(slug.lower()).discovery_source if repo.get(slug.lower()) else "",
                active=False,
                jobs_count=0,
            )
            if status == 200 and payload is not None:
                count = count_jobs_from_pages(payload)
                rec.active = True
                rec.jobs_count = count
                rec.company_name = slug.lower()
            results.append(rec)
            repo.upsert(rec)
        return results
    finally:
        if own_http:
            http.close()
