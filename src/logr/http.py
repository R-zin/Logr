"""Shared HTTP client: polite defaults, retries with backoff on transient errors."""

from __future__ import annotations

import time
from typing import Any

import httpx

USER_AGENT = "Logr/0.1 (hackathon deadline tracker; +https://github.com/r-zin/logr)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class Http:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        retries: int = 3,
        backoff: float = 1.0,
        min_interval: float = 0.5,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval  # crude per-process rate limit
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def request_json(self, method: str, url: str, **kw: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                resp = self.client.request(method, url, **kw)
                if resp.status_code in RETRY_STATUSES and attempt < self.retries:
                    retry_after = resp.headers.get("Retry-After", "")
                    delay = float(retry_after) if retry_after.isdigit() else self.backoff * 2**attempt
                    time.sleep(min(delay, 30))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.DecodingError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * 2**attempt)
        raise last_exc  # type: ignore[misc]

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.request_json("GET", url, **kw)

    def post_json(self, url: str, **kw: Any) -> Any:
        return self.request_json("POST", url, **kw)

    def close(self) -> None:
        self.client.close()
