"""Repositório leve (JSON/JSONL) — Fase 4.

* ``inhire_tenants.json`` — array de tenants validados.
* ``inhire_jobs.jsonl``   — vagas coletadas (append-only; uma linha por vaga).

Por design seguimos o padrão do resto do projeto (config.json, vagas_processadas.txt)
— tudo em texto plano, fácil de diffar e versionar sem segredo.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir(base: str | None = None) -> str:
    if base:
        os.makedirs(base, exist_ok=True)
        return base
    root = os.path.dirname(  # src/careerops/collector/inhire -> project root (5 níveis)
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
    )
    base = os.path.join(root, "data")
    os.makedirs(base, exist_ok=True)
    return base


@dataclass
class TenantRecord:
    slug: str
    company_name: str = ""
    domain: str = ""                      # "slug.inhire.app"
    discovery_source: str = ""            # seed|wayback|urlscan
    first_seen_at: str = field(default_factory=_now_iso)
    last_checked_at: str = ""
    last_success_at: str = ""
    active: bool = True
    jobs_count: int = 0
    notes: str = ""


@dataclass
class JobRecord:
    """Schema de persistência de uma vaga InHire."""

    tenant_slug: str
    source: str = "inhire"
    source_id: str = ""
    company: str = ""
    title: str = ""
    description: str = ""
    location: str = ""
    workplace_type: str = ""              # remote|hybrid|onsite
    status: str = "active"                # active|closed|...
    url: str = ""
    collected_at: str = field(default_factory=_now_iso)


# ----------------------------------------------------------------- tenants


class TenantRepository:
    def __init__(self, base_dir: str | None = None) -> None:
        self._dir = data_dir(base_dir)
        self._path = os.path.join(self._dir, "inhire_tenants.json")
        self._lock = threading.Lock()
        self._cache: list[TenantRecord] | None = None

    def _load(self) -> list[TenantRecord]:
        if not os.path.isfile(self._path):
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [TenantRecord(**row) for row in raw]
        except (OSError, ValueError):
            return []

    def _save(self, rows: list[TenantRecord]) -> None:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=self._dir, encoding="utf-8")
        try:
            json.dump([asdict(r) for r in rows], tmp, ensure_ascii=False, indent=2)
            tmp.close()
            os.replace(tmp.name, self._path)
        finally:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)

    def all(self) -> list[TenantRecord]:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            return list(self._cache)

    def get(self, slug: str) -> TenantRecord | None:
        return next((r for r in self.all() if r.slug == slug), None)

    def upsert(self, rec: TenantRecord) -> TenantRecord:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            existing = next((r for r in self._cache if r.slug == rec.slug), None)
            now = _now_iso()
            if existing:
                existing.company_name = rec.company_name or existing.company_name
                existing.last_checked_at = now
                if rec.active and rec.company_name:
                    existing.last_success_at = now
                existing.active = existing.active and rec.active
                existing.jobs_count = max(existing.jobs_count, rec.jobs_count)
                if rec.discovery_source and not existing.discovery_source:
                    existing.discovery_source = rec.discovery_source
                saved = existing
            else:
                rec.first_seen_at = now
                rec.last_checked_at = now
                if rec.active:
                    rec.last_success_at = now
                self._cache.append(rec)
                saved = rec
            self._save(self._cache)
            return saved

    def prune(self, max_age_days: int = 0) -> int:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            before = len(self._cache)
            self._cache = [r for r in self._cache if r.active]
            self._save(self._cache)
            return before - len(self._cache)


# --------------------------------------------------------------------- jobs


class JobRepository:
    def __init__(self, base_dir: str | None = None) -> None:
        self._dir = data_dir(base_dir)
        self._path = os.path.join(self._dir, "inhire_jobs.jsonl")
        self._lock = threading.Lock()

    def append(self, rec: JobRecord) -> None:
        line = json.dumps(asdict(rec), ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def iter_all(self) -> Iterator[JobRecord]:
        if not os.path.isfile(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield JobRecord(**json.loads(line))
                except (ValueError, TypeError):
                    continue
