"""URL builder — Fase 6.

Formato:

    https://<tenant>.inhire.app/vagas/<jobId>/<slug-do-titulo>

O slug é determinístico (mesmo título ⇒ mesmo slug) e estável em qualquer
execução. O endpoint da InHire não é estrito quanto ao slug e redireciona
para a vaga correta; o importante é a presença da chave (``<jobId>``).
"""
from __future__ import annotations

import re
import unicodedata


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def title_slug(title: str) -> str:
    """Slug determinístico em lowercase-aspas (kebab-case ASCII-only)."""
    s = _strip_accents((title or "").lower())
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "vaga"


def build_job_url(tenant_slug: str, job_id: str, title: str = "") -> str:
    slug = title_slug(title)
    return f"https://{tenant_slug.lower()}.inhire.app/vagas/{job_id}/{slug}"
