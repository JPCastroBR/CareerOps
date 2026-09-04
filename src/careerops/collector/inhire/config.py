"""Configurações do adapter InHire.

Lê a chave ``inhire`` do ``config.json`` (do projeto). Campos ausentes usam
defaults seguros. Mantém um cache por processo para evitar I/O repetida.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field

from careerops.collector.inhire.http import HttpSettings


_PROJECT_ROOT = os.path.dirname(  # src/careerops/collector/inhire -> project root (5 níveis)
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)


def _project_config_path() -> str:
    """Caminho do config.json na raiz do projeto (mesmo critério do main.py)."""
    return os.path.join(_PROJECT_ROOT, "config.json")


def _project_data_path() -> str:
    return os.path.join(_PROJECT_ROOT, "data")


@dataclass
class InhireConfig:
    enabled: bool = True
    api_base: str = "https://api.inhire.app"
    seed_file: str = field(                            # path p/ seed local de slugs
        default_factory=lambda: os.path.join(_project_data_path(), "inhire_seed.txt")
    )
    include_keywords: list[str] = field(               # pre-filtro de título (Fase 5)
        default_factory=lambda: [
            "sre", "site reliability", "reliability",
            "devops", "platform engineer", "engenharia de plataforma",
            "infrastructure", "infraestrutura", "cloud", "nuvem",
            "devsecops", "secops", "observability", "observabilidade",
            "automation", "automação", "linux", "kubernetes",
            "terraform", "aws", "azure", "gcp", "oci",
        ],
    )
    seed_tenants: list[str] = field(default_factory=list)  # se seed_file ausente
    sources: list[str] = field(default_factory=lambda: ["seed", "wayback", "crtsh", "bruteforce"])
    # wayback usa o CDX público; rate limit embutido em tenant_discovery.
    min_score: int = 0     # threshold do matching (Fase 9); 0 = não descarta na coleta
    http: HttpSettings = field(default_factory=HttpSettings)
    tenant_cache_ttl_s: int = 86400   # 24h


def load_config(path: str | None = None) -> InhireConfig:
    cfg_path = path or _project_config_path()
    raw: dict = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except (OSError, ValueError):
            raw = {}

    section = raw.get("inhire", {}) or {}
    cfg = InhireConfig()
    cfg.enabled = bool(section.get("enabled", cfg.enabled))
    cfg.api_base = section.get("api_base", cfg.api_base)
    cfg.seed_file = section.get("seed_file", cfg.seed_file) or ""
    kw = section.get("include_keywords")
    if isinstance(kw, list) and kw:
        cfg.include_keywords = kw
    cfg.seed_tenants = list(section.get("seed_tenants", []))
    src = section.get("sources")
    if isinstance(src, list) and src:
        cfg.sources = src
    cfg.min_score = int(section.get("min_score", cfg.min_score))

    http_raw = section.get("http", {}) or {}
    h = cfg.http
    for k in ("timeout", "retries", "backoff_base", "delay", "max_concurrency"):
        if k in http_raw:
            setattr(h, k, float(http_raw[k]) if k != "retries" and k != "max_concurrency" else int(http_raw[k]))

    return cfg


def _slugify(value: str) -> str:
    """Normaliza slug: minúsculo, sem acento, só [a-z0-9-]."""
    v = unicodedata.normalize("NFKD", value.lower())
    v = "".join(c for c in v if not unicodedata.combining(c))
    v = re.sub(r"[^a-z0-9]+", "-", v).strip("-")
    return v


def load_seed_slugs(cfg: InhireConfig) -> list[str]:
    slugs: list[str] = []
    if cfg.seed_file and os.path.isfile(cfg.seed_file):
        try:
            with open(cfg.seed_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            lines = []
        for line in lines:
            s = line.strip().lower()
            if not s or s.startswith("#"):
                continue
            slugs.append(_slugify(s))
    else:
        slugs = [_slugify(s) for s in cfg.seed_tenants]
    seen: set[str] = set()
    out: list[str] = []
    for s in slugs:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
