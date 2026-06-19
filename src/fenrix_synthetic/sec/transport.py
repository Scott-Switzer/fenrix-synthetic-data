"""SEC transport abstraction.

Defines the transport interface for SEC HTTP access. All higher-level
code must depend on this interface for testability.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SecResponse:
    """A response from an SEC transport."""

    content: bytes
    status_code: int
    headers: dict[str, str]
    url: str


class SecTransport(ABC):
    """Abstract transport for SEC HTTP requests."""

    @abstractmethod
    def get_json(self, url: str, timeout: float = 30.0) -> Any:
        """GET a URL and return parsed JSON."""
        ...

    @abstractmethod
    def get_bytes(self, url: str, timeout: float = 30.0) -> SecResponse:
        """GET a URL and return raw bytes response."""
        ...


class LiveTransport(SecTransport):
    """Live SEC transport using requests."""

    def __init__(self, user_agent: str) -> None:
        import requests

        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def get_json(self, url: str, timeout: float = 30.0) -> Any:

        resp = self._session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def get_bytes(self, url: str, timeout: float = 30.0) -> SecResponse:

        resp = self._session.get(url, timeout=timeout)
        resp.raise_for_status()
        return SecResponse(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            url=resp.url or url,
        )


class FixtureTransport(SecTransport):
    """SEC transport backed by local fixture files.

    Loads pre-recorded responses from a fixture directory.
    Fixture layout::

        fixtures/sec/
            company_tickers.json
            submissions-CIK{cik}.json
            documents/{accession_no_dashes}/{primary_document}
    """

    def __init__(self, fixture_dir: Path) -> None:
        self._fixture_dir = fixture_dir
        self._bytes_cache: dict[str, SecResponse] = {}
        self._json_cache: dict[str, Any] = {}

    def get_json(self, url: str, timeout: float = 30.0) -> Any:
        if url in self._json_cache:
            return self._json_cache[url]

        import orjson

        path = self._resolve_path(url)
        if not path.exists():
            raise FileNotFoundError(f"Fixture not found for URL: {url} (path: {path})")
        with open(path, "rb") as f:
            data = orjson.loads(f.read())
        self._json_cache[url] = data
        return data

    def get_bytes(self, url: str, timeout: float = 30.0) -> SecResponse:
        if url in self._bytes_cache:
            return self._bytes_cache[url]

        path = self._resolve_path(url)
        if not path.exists():
            raise FileNotFoundError(f"Fixture not found for URL: {url} (path: {path})")
        content = path.read_bytes()
        resp = SecResponse(
            content=content,
            status_code=200,
            headers={"content-type": "text/html"},
            url=url,
        )
        self._bytes_cache[url] = resp
        return resp

    def _resolve_path(self, url: str) -> Path:
        """Map a SEC URL to a local fixture path."""
        if "company_tickers.json" in url:
            return self._fixture_dir / "company_tickers.json"
        if "/submissions/" in url:
            cik = self._extract_cik(url)
            return self._fixture_dir / f"submissions-CIK{cik}.json"
        if "/Archives/edgar/data/" in url:
            parts = url.split("/Archives/edgar/data/")[1]
            return self._fixture_dir / "documents" / parts
        raise ValueError(f"Cannot resolve fixture path for URL: {url}")

    @staticmethod
    def _extract_cik(url: str) -> str:
        """Extract CIK from a submissions URL."""
        import re

        m = re.search(r"CIK(\d{10})\.json", url)
        if m:
            return m.group(1)
        raise ValueError(f"Cannot extract CIK from URL: {url}")
