"""Tenant Discovery — Fase 2.

O InHire não tem busca global de vagas. Cada empresa é um subdomínio
``<slug>.inhire.app``. Este módulo reúne candidatos a tenant a partir de:

* ``seed``   — arquivo local ou lista embutida em config.json (Fase 2 fonte de partida)
* ``wayback`` — CDX público do Wayback Machine (grátis, sem key)
* ``urlscan`` — busca pública do urlscan.io (API key opcional via URL)

Em todos os casos:

* extrai hosts no formato ``empresa.inhire.app``;
* deduz o ``slug`` (= primeiro label);
* descarta duplicatas.
* Não considera um tenant válido porque resolve DNS — quem valida é a Fase 3.
"""
from __future__ import annotations

import json
import logging
import os
import time
import re
import urllib.parse
from typing import Iterable

from careerops.collector.inhire.config import InhireConfig, load_seed_slugs
from careerops.collector.inhire.http import InhireHttp

log = logging.getLogger("careerops.inhire.discovery")


# Aceita "empresa.inhire.app" OU "www.empresa.inhire.app", "vagas.empresa.inhire.app".
_HOST_RE = re.compile(
    r"\b(?P<host>(?:[a-z0-9-]+\.)*?(?P<slug>[a-z0-9][a-z0-9-]{1,40})\.inhire\.app)\b",
    re.IGNORECASE,
)


def _slug_from_host(host: str) -> str | None:
    m = _HOST_RE.search(host)
    return m.group("slug").lower() if m else None


def slug_from_host(host: str) -> str | None:
    """Função pública — extrai slug de host arbitrário ``*.inhire.app``."""
    return _slug_from_host(host)


# ------------------------------------------------------------------- sources


def _from_seed(cfg: InhireConfig) -> set[str]:
    return {s for s in load_seed_slugs(cfg) if s and "." not in s and "*" not in s}


def _wayback_cache_path(cfg: InhireConfig) -> str:
    base = os.path.dirname(os.path.abspath(cfg.seed_file))
    return os.path.join(base, "inhire_wayback_cache.json")


def _load_wayback_cache(cache_file: str) -> tuple[float, set[str]] | None:
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return float(data.get("ts", 0.0)), set(data.get("slugs", []))
    except (OSError, ValueError):
        return None


def _save_wayback_cache(cache_file: str, slugs: set[str]) -> None:
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "slugs": sorted(slugs)}, f)
    except OSError as e:  # noqa: BLE001
        log.debug("inhire wayback cache write failed: %s", e)


def _from_wayback(http: InhireHttp, *, limit: int = 1500, cache_file: str = "", cache_ttl_s: int = 86400) -> set[str]:
    """Wayback CDX público — sem auth, rate limit embutido.

    Resultado é cacheado (TTL em ``cache_ttl_s``) para não martelar o endpoint
    flaky toda hora. Se o live falhar mas houver cache, usa o cache (mesmo
    obsoleto) em vez de perder a descoberta.
    """
    if cache_file and cache_ttl_s > 0:
        hit = _load_wayback_cache(cache_file)
        if hit is not None:
            ts, slugs = hit
            if (time.time() - ts) < cache_ttl_s:
                log.info("inhire discovery wayback cache hit (age=%.0fs, n=%d)", time.time() - ts, len(slugs))
                return slugs

    url = (
        "https://web.archive.org/cdx/search/cdx"
        "?url=*.inhire.app&output=json&fl=original&limit={limit}&filter=statuscode:200"
    ).format(limit=limit)
    log.info("inhire discovery wayback begin")
    status, payload = http.get(url)
    if status != 200 or not payload:
        log.warning("inhire discovery wayback live falhou (status=%s)", status)
        if cache_file:
            hit = _load_wayback_cache(cache_file)
            if hit is not None:
                log.info("inhire discovery wayback usando cache obsoleto (n=%d)", len(hit[1]))
                return hit[1]
        return set()

    found: set[str] = set()
    # CDX retorna [["url","original"], ...] ou JSON-Lines dependendo do output
    rows: Iterable
    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        rows = payload[1:]  # descarta header
        for row in rows:
            try:
                original = row[0]
            except IndexError:
                continue
            slug = _slug_from_host(original)
            if slug:
                found.add(slug)
    elif isinstance(payload, str):
        for line in payload.splitlines():
            try:
                original = json.loads(line)[0]
            except (ValueError, IndexError):
                continue
            slug = _slug_from_host(original)
            if slug:
                found.add(slug)
    log.info("inhire discovery wayback found=%d", len(found))
    if cache_file:
        _save_wayback_cache(cache_file, found)
    return found


def _from_urlscan(http: InhireHttp, *, query: str = "*.inhire.app", limit: int = 1000) -> set[str]:
    """urlscan.io — sem key retorna até pequeno volume; com key retorna muito mais.

    Respeita rate limit via :class:`InhireHttp`.
    """
    api_key = os.environ.get("URLSCAN_API_KEY", "").strip()
    if not api_key:
        return set()
    url = f"https://urlscan.io/api/v1/search/?q={urllib.parse.quote(query)}&limit={limit}"
    status, payload = http.get(url, headers={"API-Key": api_key})
    if status != 200 or not isinstance(payload, dict):
        return set()
    found: set[str] = set()
    for r in payload.get("results", []) or []:
        page = r.get("page", {}) or {}
        host = page.get("domain") or ""
        slug = _slug_from_host(host)
        if slug:
            found.add(slug)
    log.info("inhire discovery urlscan found=%d", len(found))
    return found


def _from_crtsh(http: InhireHttp, *, limit: int = 5000, cache_file: str = "", cache_ttl_s: int = 86400) -> set[str]:
    """crt.sh (Certificate Transparency logs) — grátis, sem auth, completo.

    Retorna todos os subdomínios *.inhire.app que já tiveram certificado SSL emitido.
    Fonte mais abrangente para descoberta de tenants reais. Cacheado (TTL) para
    sobreviver à instabilidade do crt.sh.
    """
    if cache_file and cache_ttl_s > 0:
        hit = _load_wayback_cache(cache_file)  # reusa o mesmo formato
        if hit is not None:
            ts, slugs = hit
            if (time.time() - ts) < cache_ttl_s:
                log.info("inhire discovery crtsh cache hit (age=%.0fs, n=%d)", time.time() - ts, len(slugs))
                return slugs

    # Tentativa 1: query padrão
    url = f"https://crt.sh/?q=%.inhire.app&output=json"
    log.info("inhire discovery crtsh begin")
    status, payload = http.get(url)
    if status != 200 or not payload:
        log.warning("inhire discovery crtsh live falhou (status=%s)", status)
        # Fallback: query alternativa (sem wildcard, busca exata)
        url2 = f"https://crt.sh/?q=inhire.app&output=json"
        status, payload = http.get(url2)
        if status != 200 or not payload:
            log.warning("inhire discovery crtsh fallback falhou (status=%s)", status)
            if cache_file:
                hit = _load_wayback_cache(cache_file)
                if hit is not None:
                    log.info("inhire discovery crtsh usando cache obsoleto (n=%d)", len(hit[1]))
                    return hit[1]
            return set()

    found: set[str] = set()
    if isinstance(payload, list):
        for entry in payload[:limit]:
            name = entry.get("name_value", "") or ""
            for line in name.split("\n"):
                host = line.strip()
                slug = _slug_from_host(host)
                if slug:
                    found.add(slug)
    log.info("inhire discovery crtsh found=%d", len(found))
    if cache_file:
        _save_wayback_cache(cache_file, found)
    return found


def discover_tenants(cfg: InhireConfig, http: InhireHttp | None = None) -> dict[str, str]:
    """Retorna ``{slug: discovery_source, ...}`` para os candidatos coletados.

    ``discovery_source`` é a primeira fonte em que o slug apareceu.
    """
    sources = list(cfg.sources)
    if "seed" in sources and cfg.seed_file and not cfg.seed_tenants:
        cfg.seed_tenants = load_seed_slugs(cfg)

    own_http = http is None
    http = http or InhireHttp(cfg.http)
    try:
        wayback_cache = _wayback_cache_path(cfg) if "wayback" in sources else ""
        crtsh_cache = os.path.join(os.path.dirname(os.path.abspath(cfg.seed_file)), "inhire_crtsh_cache.json") if "crtsh" in sources else ""
        merged: dict[str, str] = {}
        for src in sources:
            if src == "seed":
                for s in _from_seed(cfg):
                    merged.setdefault(s, "seed")
            elif src == "wayback":
                for s in _from_wayback(
                    http,
                    cache_file=wayback_cache,
                    cache_ttl_s=cfg.tenant_cache_ttl_s,
                ):
                    merged.setdefault(s, "wayback")
            elif src == "urlscan":
                for s in _from_urlscan(http):
                    merged.setdefault(s, "urlscan")
            elif src == "crtsh":
                for s in _from_crtsh(
                    http,
                    cache_file=crtsh_cache,
                    cache_ttl_s=cfg.tenant_cache_ttl_s,
                ):
                    merged.setdefault(s, "crtsh")
            else:
                log.warning("inhire discovery unknown source=%s (ignorado)", src)
    finally:
        if own_http:
            http.close()

    log.info("inhire discovery total unique slugs=%d", len(merged))
    return merged
