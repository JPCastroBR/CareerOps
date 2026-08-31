"""Camada HTTP compartilhada do adapter InHire.

Fornece um cliente ``httpx`` com:

* timeout;
* retries com backoff exponencial;
* rate-limit (intervalo mínimo entre chamadas);
* cache em memória de respostas GET bem-sucedidas;
* logging estruturado mínimo.

Usado por ``tenant_validator``, ``job_collector`` e ``tenant_discovery``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("careerops.inhire.http")


@dataclass
class HttpSettings:
    timeout: float = 10.0
    retries: int = 3
    backoff_base: float = 1.5
    delay: float = 0.4
    max_concurrency: int = 4
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )


class _RateGate:
    """Limita chamadas a ``delay`` segundos entre si (global ao processo)."""

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._delay - (now - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


class _Cache:
    """Cache de GET por URL+headers. Thread-safe, sem TTL (Fase 13 pode adicionar)."""

    def __init__(self) -> None:
        self._data: dict[tuple, tuple[int, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> tuple[int, Any] | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: tuple, value: tuple[int, Any]) -> None:
        with self._lock:
            self._data[key] = value


class InhireHttp:
    """Wrapper HTTP com retry/backoff/rate-limit/cache."""

    @staticmethod
    def _coerce(settings: HttpSettings | None) -> HttpSettings:
        """Aceita HttpSettings ou InhireConfig (por duck-typing, sem import circular)."""
        if settings is None:
            return HttpSettings()
        if isinstance(settings, HttpSettings):
            return settings
        # InhireConfig-like: usa o sub-objeto `http` (HttpSettings)
        if hasattr(settings, "http"):
            sub = getattr(settings, "http", None)
            if isinstance(sub, HttpSettings):
                return sub
            return HttpSettings()
        return settings

    def __init__(self, settings: HttpSettings | None = None) -> None:
        self.settings = self._coerce(settings)
        self._gate = _RateGate(self.settings.delay)
        self._cache = _Cache()
        self._client = httpx.Client(
            http2=True,
            timeout=self.settings.timeout,
            headers={"User-Agent": self.settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "InhireHttp":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ core

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        use_cache: bool = False,
        accept_status: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        cache_key = ("GET", url, tuple(sorted((headers or {}).items())), tuple(sorted((params or {}).items()))) if use_cache else None
        if cache_key and (hit := self._cache.get(cache_key)):
            return hit

        last_exc: Exception | None = None
        for attempt in range(1, self.settings.retries + 1):
            self._gate.wait()
            try:
                resp = self._client.request(method, url, headers=headers, params=params)
            except httpx.HTTPError as e:
                last_exc = e
                log.debug("inhire http error attempt=%s url=%s err=%s", attempt, url, e)
            else:
                if resp.status_code in accept_status:
                    payload = self._safe_json(resp)
                    if cache_key:
                        self._cache.set(cache_key, (resp.status_code, payload))
                    return resp.status_code, payload
                # 404/410 = "invalido"; não tenta de novo
                if resp.status_code in (404, 410):
                    return resp.status_code, None
                # 429/5xx = tenta de novo
                last_exc = httpx.HTTPStatusError(
                    f"status {resp.status_code}", request=resp.request, response=resp
                )
                log.debug("inhire http status=%s attempt=%s url=%s", resp.status_code, attempt, url)

            time.sleep(self.settings.backoff_base * attempt)

        log.warning("inhire http gave up url=%s err=%s", url, last_exc)
        return 0, None

    def get(self, url: str, **kw: Any) -> tuple[int, Any]:
        kw.setdefault("use_cache", True)
        return self.request("GET", url, **kw)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError):
            return resp.text


def settings_from_env(env: dict | None = None) -> HttpSettings:
    """Permite futura leitura via env (INHIRE_MAX_CONCURRENCY etc.)."""
    env = env or {}
    s = HttpSettings()
    if "INHIRE_REQUEST_TIMEOUT" in env:
        s.timeout = float(env["INHIRE_REQUEST_TIMEOUT"])
    if "INHIRE_RETRY" in env:
        s.retries = int(env["INHIRE_RETRY"])
    if "INHIRE_DELAY" in env:
        s.delay = float(env["INHIRE_DELAY"])
    return s
