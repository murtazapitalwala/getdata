from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    elapsed_s: float


class HttpClient:
    def __init__(
        self,
        timeout_s: float = 20.0,
        user_agent: str = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        max_retries: int = 3,
        backoff_s: float = 0.6,
    ) -> None:
        self._timeout_s = timeout_s
        self._user_agent = user_agent
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._session = requests.Session()

    def get_json(self, url: str, *, params: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], FetchResult]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            t0 = time.time()
            try:
                resp = self._session.get(url, params=params, headers=headers, timeout=self._timeout_s)
                elapsed = time.time() - t0
                if resp.status_code >= 400:
                    # Some providers return HTML on bot blocks; make that visible.
                    snippet = resp.text[:500]
                    raise RuntimeError(f"HTTP {resp.status_code} for {resp.url}: {snippet}")

                try:
                    data = resp.json()
                except json.JSONDecodeError as e:
                    snippet = resp.text[:500]
                    raise RuntimeError(f"Non-JSON response for {resp.url}: {snippet}") from e

                return data, FetchResult(url=resp.url, status_code=resp.status_code, elapsed_s=elapsed)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self._max_retries:
                    time.sleep(self._backoff_s * attempt)
                    continue
                raise

        assert last_err is not None
        raise last_err

    def get_text(self, url: str, *, params: Optional[Dict[str, Any]] = None) -> Tuple[str, FetchResult]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/plain,text/html,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            t0 = time.time()
            try:
                resp = self._session.get(url, params=params, headers=headers, timeout=self._timeout_s)
                elapsed = time.time() - t0
                if resp.status_code >= 400:
                    snippet = resp.text[:500]
                    raise RuntimeError(f"HTTP {resp.status_code} for {resp.url}: {snippet}")
                return resp.text, FetchResult(url=resp.url, status_code=resp.status_code, elapsed_s=elapsed)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self._max_retries:
                    time.sleep(self._backoff_s * attempt)
                    continue
                raise

        assert last_err is not None
        raise last_err
