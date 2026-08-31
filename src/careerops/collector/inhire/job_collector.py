"""Job Collector — Fase 5.

Para cada tenant válido (registrado em :class:`TenantRepository`):

* consulta o endpoint público;
* percorre ``jobPosts[].items`` ou equivalente;
* normaliza;
* converte para o **mesmo schema DataFrame** que ``JobSpy`` e ``banks``;
* anexa campos extras do InHire: ``tenant_slug``, ``source_id``,
  ``workplace_type``, ``match_score`` etc.
* persiste em :class:`JobRepository`.

Critério da Fase 9: ``match_score`` é calculado **aqui** por vaga para evitar
chamada IA (mas a IA no n8n continua existindo para outros sinais).
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import asdict
from typing import Iterable

import pandas as pd

from careerops.collector.inhire.config import InhireConfig
from careerops.collector.inhire.cv_profile import CVProfile
from careerops.collector.inhire.http import InhireHttp
from careerops.collector.inhire.matching import compute_match
from careerops.collector.inhire.normalize import (
    normalize_company,
    normalize_title,
    normalize_workplace,
    normalize_seniority,
    to_iso_date,
)
from careerops.collector.inhire.repository import JobRecord, JobRepository, TenantRecord, TenantRepository
from careerops.collector.inhire.url import build_job_url

log = logging.getLogger("careerops.inhire.collector")


# ------------------------------------------------------------------ helpers


_TITLE_RE = re.compile(r"\s+")


def _include_title(title: str, include: list[str]) -> bool:
    t = (title or "").lower()
    return any(k in t for k in include)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y", "sim"):
        return True
    if s in ("false", "0", "no", "n", "não", "nao"):
        return False
    return None


# ------------------------------------------------------------------ core


def _jobs_from_payload(payload, tenant: TenantRecord) -> list[dict]:
    """Extrai a lista de vagas do payload retornado pelo endpoint público.

    Schemas observados na API pública InHire:
      * ``{"tenantName": str, "jobsPage": [ ... ]}``   (formato real)
      * ``{"data": [ ... ]}`` / ``{"jobs": [ ... ]}`` / ``{"items": [ ... ]}``
      * ``{"jobPosts": {"items": [ ... ]}}``
    """
    if not isinstance(payload, dict):
        return []
    # Formato real da API
    jp = payload.get("jobsPage")
    if isinstance(jp, list):
        return jp
    rows = payload.get("data") or payload.get("jobs") or payload.get("items")
    if isinstance(rows, list):
        return rows
    jp = payload.get("jobPosts")
    if isinstance(jp, dict) and isinstance(jp.get("items"), list):
        return jp["items"]
    if isinstance(jp, list):
        return jp
    return []


def _normalize_row(raw: dict, tenant: TenantRecord, tenant_name: str = "") -> dict:
    """Mapeia um item bruto para o schema DataFrame comum.

    Schema observado na API pública InHire (jobsPage):
      displayName, jobId, status, workplaceType, location
      (tenantName vem no topo do payload)
    """
    jid = str(raw.get("jobId") or raw.get("id") or raw.get("_id") or raw.get("slug") or "")
    title = normalize_title(raw.get("displayName") or raw.get("title") or raw.get("name") or "")
    company = normalize_company(raw.get("company") or raw.get("companyName") or tenant_name or tenant.company_name or tenant.slug)
    location = raw.get("location") or raw.get("city") or ""
    description = raw.get("description") or raw.get("summary") or ""
    workplace_raw = raw.get("workplaceType") or raw.get("workplace") or raw.get("model") or ""
    is_remote_b = _as_bool(raw.get("isRemote") or raw.get("remote"))
    workplace = normalize_workplace(str(workplace_raw), is_remote=is_remote_b)
    status = (raw.get("status") or "active").strip().lower()
    employment_type = raw.get("employmentType") or raw.get("type") or ""
    seniority_raw = raw.get("seniority") or raw.get("level") or ""

    job_url = raw.get("url") or raw.get("jobUrl") or raw.get("applyUrl") or build_job_url(tenant.slug, jid, title)
    posted = to_iso_date(raw.get("createdAt") or raw.get("datePosted") or raw.get("publishedAt"))

    return {
        "title": title,
        "company": company,
        "location": str(location),
        "description": str(description),
        "job_url": job_url,
        "is_remote": is_remote_b if is_remote_b is not None else (workplace == "remote"),
        "remote": workplace if workplace else ("remote" if is_remote_b else ""),
        "job_type": str(employment_type),
        "job_level": str(seniority_raw),
        "workplace_type": workplace,
        "seniority": normalize_seniority(str(seniority_raw), title) or str(seniority_raw),
        "status": status,
        "date_posted": posted,
        "source": f"inhire:{tenant.slug}",
        "tenant_slug": tenant.slug,
        "source_id": jid,
    }


def _strip_html(value: str) -> str:
    """Remove tags HTML e decodifica entidades (ex.: &ccedil; → ç)."""
    if not value:
        return ""
    txt = re.sub(r"<[^>]+>", " ", value)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _fetch_job_detail(cfg: InhireConfig, http: InhireHttp, tenant_slug: str, job_id: str) -> dict:
    """Busca o detalhe da vaga na API pública InHire.

    ``GET /job-posts/public/pages/<jobId>`` (header ``X-Tenant``) retorna a
    descrição completa (HTML) — o ``jobsPage`` só traz título/local/modalidade.
    """
    url = f"{cfg.api_base.rstrip('/')}/job-posts/public/pages/{job_id}"
    try:
        status, payload = http.request("GET", url, headers={"X-Tenant": tenant_slug}, accept_status=(200,))
    except Exception as e:  # noqa: BLE001 - não deve derrubar a coleta
        log.warning("inhire detail: tenant=%s job=%s err=%s", tenant_slug, job_id, e)
        return {}
    if status != 200 or not isinstance(payload, dict):
        return {}
    desc = _strip_html(payload.get("description") or "")
    created = to_iso_date(payload.get("createdAt") or payload.get("lastPublishedAt"))
    contract = payload.get("contractType") or ""
    return {"description": desc, "created_at": created, "contract_type": str(contract)}


def collect_jobs_for_tenant(
    tenant: TenantRecord,
    *,
    cfg: InhireConfig,
    http: InhireHttp,
    profile: CVProfile,
    job_repo: JobRepository | None = None,
    include_keywords: list[str] | None = None,
) -> pd.DataFrame:
    """Coleta vagas ativas para um único tenant. Retorna DataFrame pronto."""
    include = [k.lower() for k in (include_keywords or cfg.include_keywords)]
    url = f"{cfg.api_base.rstrip('/')}/job-posts/public/pages"
    headers = {"X-Tenant": tenant.slug}

    status, payload = http.request("GET", url, headers=headers, accept_status=(200,))
    if status != 200 or payload is None:
        log.debug("inhire collect: tenant=%s status=%s (skip)", tenant.slug, status)
        # status==0 => falha de rede (outage); status>=400 => tenant inexistente
        return pd.DataFrame(), (status != 0)

    raw_jobs = _jobs_from_payload(payload, tenant)
    tenant_name = (payload or {}).get("tenantName") or ""
    if tenant_name and not tenant.company_name:
        tenant.company_name = normalize_company(tenant_name)
    rows = []
    for raw in raw_jobs:
        if str(raw.get("status") or "active").lower() not in ("active", "open", "", "published"):
            continue
        rec = _normalize_row(raw, tenant, tenant_name=tenant_name)
        if include and not _include_title(rec["title"], include):
            continue
        # Enriquecer com a descrição real (o jobsPage não a traz)
        if rec["source_id"]:
            detail = _fetch_job_detail(cfg, http, tenant.slug, rec["source_id"])
            if detail:
                if detail.get("description"):
                    rec["description"] = detail["description"]
                if detail.get("created_at"):
                    rec["date_posted"] = detail["created_at"]
        # Match local (não chama IA)
        score = compute_match(
            title=rec["title"],
            description=rec["description"],
            workplace_type=rec.get("workplace_type", ""),
            is_remote=rec.get("is_remote"),
            location=rec["location"],
            seniority=rec.get("seniority", ""),
            profile=profile,
        )
        rec["match_score"] = score.overall_score
        rec["match_category"] = score.category
        rec["match_dims"] = score.as_dict()
        rows.append(rec)

    if not rows:
        return pd.DataFrame(), True

    df = pd.DataFrame(rows)
    if job_repo is not None:
        for r in rows:
            job_repo.append(JobRecord(
                tenant_slug=tenant.slug,
                source_id=r["source_id"],
                company=r["company"],
                title=r["title"],
                description=r["description"],
                location=r["location"],
                workplace_type=r["workplace_type"],
                status=r["status"],
                url=r["job_url"],
            ))
    return df, True


def collect_all_tenants(
    tenants: Iterable[TenantRecord],
    *,
    cfg: InhireConfig,
    profile: CVProfile | None = None,
    http: InhireHttp | None = None,
    job_repo: JobRepository | None = None,
    include_keywords: list[str] | None = None,
    min_score: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Itera tenants e devolve ``(df_total, stats)``.

    Aplica o threshold ``min_score`` (ou ``cfg.min_score`` se omitido) quando > 0.
    """
    profile = profile or CVProfile()
    job_repo = job_repo or JobRepository()
    tenants = list(tenants)
    ms = cfg.min_score if min_score is None else min_score
    log.info("inhire collect: %d tenants a visitar", len(tenants))

    own_http = http is None
    http = http or InhireHttp(cfg.http)
    # Circuit-breaker: se a rede cair, não martela todos os tenants (logo aborta)
    net_fail_limit = 3
    consec_net_fail = 0
    try:
        all_df = []
        stats = {"tenants_visited": 0, "tenants_active": 0, "jobs_collected": 0, "jobs_filtered_pre": 0}
        for t in tenants:
            if not t.active:
                continue
            stats["tenants_visited"] += 1
            df, net_ok = collect_jobs_for_tenant(
                t, cfg=cfg, http=http, profile=profile,
                job_repo=job_repo, include_keywords=include_keywords,
            )
            if not net_ok:
                consec_net_fail += 1
                if consec_net_fail >= net_fail_limit:
                    log.warning(
                        "inhire collect: rede aparenta fora do ar (%d tenants seguidos falharam); "
                        "abortando pass desta hora",
                        consec_net_fail,
                    )
                    break
                continue
            consec_net_fail = 0
            if not df.empty:
                stats["tenants_active"] += 1
                stats["jobs_collected"] += len(df)
                if ms and ms > 0:
                    before = len(df)
                    df = df[df["match_score"].astype(float) >= ms].reset_index(drop=True)
                    stats["jobs_filtered_pre"] += (before - len(df))
                if not df.empty:
                    all_df.append(df)
        out = pd.concat(all_df, ignore_index=True) if all_df else pd.DataFrame()
    finally:
        if own_http:
            http.close()

    log.info("inhire collect: stats=%s", stats)
    return out, stats
