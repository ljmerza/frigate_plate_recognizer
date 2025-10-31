"""HTTP client utilities with sane retries and timeouts."""

from __future__ import annotations

from typing import Optional

import requests
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BACKOFF_FACTOR = 0.5
STATUS_FORCELIST = (429, 500, 502, 503, 504)
ALLOWED_METHODS = ("GET", "POST")


class TimeoutHTTPAdapter(HTTPAdapter):
    """HTTP adapter that applies a default timeout to requests."""

    def __init__(self, *, timeout: float, retries: Retry):
        super().__init__(max_retries=retries)
        self._timeout = timeout

    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout)
        return super().send(request, **kwargs)


def build_retry_strategy(retries: int) -> Retry:
    return Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=DEFAULT_BACKOFF_FACTOR,
        status_forcelist=STATUS_FORCELIST,
        allowed_methods=ALLOWED_METHODS,
        respect_retry_after_header=True,
        raise_on_status=False,
    )


def build_session(*, timeout: float, retries: int, verify: Optional[bool] = None) -> Session:
    """Return a configured requests session."""

    retry_strategy = build_retry_strategy(retries)
    adapter = TimeoutHTTPAdapter(timeout=timeout, retries=retry_strategy)

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    if verify is not None:
        session.verify = verify

    return session


__all__ = ["build_session"]
