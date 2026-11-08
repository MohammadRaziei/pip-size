"""Async PyPI client: Simple API for versions/files, per-release JSON for requires_dist."""

from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

try:
    from aiohttp_socks import ProxyConnector as _SocksConnector
    _SOCKS_AVAILABLE = True
except ImportError:
    _SocksConnector = None          # type: ignore[assignment,misc]
    _SOCKS_AVAILABLE = False

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename

from .cache import Cache

log = logging.getLogger(__name__)

PYPI_SIMPLE_API  = "https://pypi.org/simple/{package}/"
PYPI_RELEASE_API = "https://pypi.org/pypi/{package}/{version}/json"
SIMPLE_ACCEPT    = "application/vnd.pypi.simple.v1+json"
MAX_CONCURRENCY  = 10
REQUEST_TIMEOUT  = 30   # seconds



# ─────────────────────────────────────────────────────────────────────
# PyPI HTTP client
# ─────────────────────────────────────────────────────────────────────

def _normalize_file(f: dict) -> dict:
    return {
        "filename":        f.get("filename", ""),
        "size":            f.get("size") or 0,
        "requires_python": f.get("requires-python") or f.get("requires_python"),
        "yanked":          bool(f.get("yanked")),
    }


def _group_by_version(files: list[dict]) -> dict[str, list[dict]]:
    """Group Simple-API file entries by (normalised) release version. Skips eggs/exes/etc."""
    grouped: dict[str, list[dict]] = {}
    for f in files:
        filename = f.get("filename", "")
        try:
            if filename.endswith(".whl"):
                version = parse_wheel_filename(filename)[1]
            elif filename.endswith((".tar.gz", ".zip")):
                version = parse_sdist_filename(filename)[1]
            else:
                continue
        except Exception:                       # noqa: BLE001 - odd legacy filenames
            log.debug("ignoring unparseable filename %r", filename)
            continue
        grouped.setdefault(str(version), []).append(_normalize_file(f))
    return grouped


def resolve_proxy_url(flag: str | None) -> str | None:
    """Proxy priority: explicit argument, then HTTPS_PROXY/HTTP_PROXY, then ALL_PROXY/SOCKS_PROXY."""
    if flag:
        return flag
    for var in PyPIClient._ENV_PROXY_VARS:
        val = os.environ.get(var)
        if val:
            return val
    return None


class PyPIClient:
    """
    Async PyPI client with caching and proxy support.

    Two endpoints are used:
      * Simple API (PEP 691 JSON)        -> every version of a project with file names and sizes
      * /pypi/<name>/<version>/json      -> ``requires_dist`` of one release

    Proxy priority:
      1. proxy argument passed to __init__
      2. HTTPS_PROXY / HTTP_PROXY env var
      3. ALL_PROXY / SOCKS_PROXY env var
    """

    _ENV_PROXY_VARS = (
        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "SOCKS_PROXY",
        "https_proxy", "http_proxy", "all_proxy", "socks_proxy",
    )

    def __init__(
        self,
        proxy:     str | None = None,
        use_cache: bool       = True,
    ) -> None:
        self.proxy_url = resolve_proxy_url(proxy)
        self.use_cache = use_cache
        self._sem      = asyncio.Semaphore(MAX_CONCURRENCY)
        self._session: aiohttp.ClientSession | None = None

    # ── proxy helpers ────────────────────────────────────────────────

    def _make_connector(self) -> aiohttp.BaseConnector:
        if self.proxy_url is None:
            return aiohttp.TCPConnector(limit=MAX_CONCURRENCY)

        scheme = self.proxy_url.split("://")[0].lower()
        if scheme in ("socks4", "socks5", "socks5h"):
            if not _SOCKS_AVAILABLE:
                raise RuntimeError(
                    f"SOCKS proxy requested ({self.proxy_url!r}) but 'aiohttp-socks' "
                    "is not installed.\n  Fix: pip install aiohttp-socks"
                )
            log.debug("using SOCKS connector  url=%s", self.proxy_url)
            return _SocksConnector.from_url(self.proxy_url, limit=MAX_CONCURRENCY)

        log.debug("using HTTP proxy  url=%s", self.proxy_url)
        return aiohttp.TCPConnector(limit=MAX_CONCURRENCY)

    @property
    def _request_proxy(self) -> str | None:
        """For SOCKS the proxy is baked into the connector; HTTP needs per-request kwarg."""
        if self.proxy_url is None:
            return None
        scheme = self.proxy_url.split("://")[0].lower()
        return None if scheme in ("socks4", "socks5", "socks5h") else self.proxy_url

    # ── session lifecycle ────────────────────────────────────────────

    async def __aenter__(self) -> PyPIClient:
        self._session = aiohttp.ClientSession(
            connector = self._make_connector(),
            headers   = {"User-Agent": "pip-size/1.0"},
            timeout   = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── public API ───────────────────────────────────────────────────

    async def project_files(self, name: str) -> dict[str, list[dict]]:
        """
        All releases of a project: {version: [file, ...]}.
        Each file is {"filename", "size", "requires_python", "yanked"}.
        """
        url = PYPI_SIMPLE_API.format(package=canonicalize_name(name))
        if self.use_cache:
            cached = Cache.read(url)
            if cached is not None:
                return cached["versions"]

        raw      = await self._get(url, SIMPLE_ACCEPT)
        versions = _group_by_version(raw.get("files", []))
        if self.use_cache:
            Cache.write(url, {"versions": versions})
        return versions

    async def release(self, name: str, version: str) -> dict:
        """One release: {"requires_dist": [str, ...], "files": [file, ...]}."""
        url = PYPI_RELEASE_API.format(package=canonicalize_name(name), version=version)
        if self.use_cache:
            cached = Cache.read(url)
            if cached is not None:
                return cached

        raw  = await self._get(url, "application/json")
        data = {
            "requires_dist": (raw.get("info") or {}).get("requires_dist") or [],
            "files":         [_normalize_file(u) for u in raw.get("urls", [])],
        }
        if self.use_cache:
            Cache.write(url, data)
        return data

    # ── internal ─────────────────────────────────────────────────────

    async def _get(self, url: str, accept: str) -> dict:
        if self._session is None:
            raise RuntimeError("PyPIClient must be used as an async context manager")

        log.debug("GET %s%s", url, f"  (proxy: {self.proxy_url})" if self.proxy_url else "")
        async with self._sem:
            try:
                async with self._session.get(
                    url, proxy=self._request_proxy, headers={"Accept": accept},
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}: {resp.reason}  ({url})")
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as exc:
                raise RuntimeError(f"Network error: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise RuntimeError(f"Timeout after {REQUEST_TIMEOUT}s  ({url})") from exc
