"""
pip-size core: PyPI client, cache, wheel selection, dependency resolver, printer.

No downloads. No pip subprocess. PyPI Simple API (PEP 691/700) + per-release JSON
(only for ``requires_dist``) + ``packaging``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

import aiohttp

try:
    from aiohttp_socks import ProxyConnector as _SocksConnector
    _SOCKS_AVAILABLE = True
except ImportError:
    _SocksConnector = None          # type: ignore[assignment,misc]
    _SOCKS_AVAILABLE = False

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .errors import ResolutionError
from .skip import SkipRules
from .wheel import LocalWheel

log = logging.getLogger(__name__)


PYPI_SIMPLE_API  = "https://pypi.org/simple/{package}/"
PYPI_RELEASE_API = "https://pypi.org/pypi/{package}/{version}/json"
SIMPLE_ACCEPT    = "application/vnd.pypi.simple.v1+json"
MAX_CONCURRENCY  = 10
REQUEST_TIMEOUT  = 30   # seconds


# ─────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────

@dataclass
class PackageInfo:
    name:         str
    version:      str
    size:         int           # bytes of the chosen distribution file
    filename:     str
    via_extra:    str | None = None
    skipped:      str | None = None   # reason, if excluded from the total
    local:        bool       = False  # True for a wheel read from disk
    dependencies: list[PackageInfo] = field(default_factory=list)

    def total_size(self, include_skipped: bool = False) -> int:
        """Own size plus all dependencies. Skipped packages are not counted by default."""
        own = self.size if (include_skipped or not self.skipped) else 0
        return own + sum(d.total_size(include_skipped) for d in self.dependencies)

    def skipped_size(self) -> int:
        """Combined size of the skipped packages in this subtree."""
        own = self.size if self.skipped else 0
        return own + sum(d.skipped_size() for d in self.dependencies)

    def walk(self) -> Iterator[PackageInfo]:
        """Depth-first iteration over this node and all of its dependencies."""
        yield self
        for dep in self.dependencies:
            yield from dep.walk()

    def to_dict(self) -> dict:
        """Plain-data form; all sizes are integers (bytes)."""
        out: dict = {
            "name":       self.name,
            "version":    self.version,
            "size":       self.size,
            "total_size": self.total_size(),
            "filename":   self.filename,
        }
        if self.via_extra:
            out["via_extra"] = self.via_extra
        if self.skipped:
            out["skipped"] = self.skipped
        if self.local:
            out["local"] = True
        if self.dependencies:
            out["dependencies"] = [d.to_dict() for d in self.dependencies]
        return out


@dataclass
class Conflict:
    """A package was resolved to a version that another requirement does not allow."""
    name:        str
    version:     str          # what was selected
    requirement: str          # the specifier that rejects it
    required_by: str | None   # the package that asked for it (None = the root request)


@dataclass
class Resolution:
    """Result of a resolve() call."""
    root:      PackageInfo
    conflicts: list[Conflict] = field(default_factory=list)
    warnings:  list[str]      = field(default_factory=list)   # non-fatal: deps that could not be resolved

    @property
    def total_size(self) -> int:
        """Bytes to download, not counting skipped packages."""
        return self.root.total_size()

    @property
    def skipped_size(self) -> int:
        return self.root.skipped_size()

    @property
    def packages(self) -> list[PackageInfo]:
        """Every package in the tree (root first), skipped ones included."""
        return list(self.root.walk())

    @property
    def skipped(self) -> list[PackageInfo]:
        return [p for p in self.root.walk() if p.skipped]

    def to_dict(self) -> dict:
        out = self.root.to_dict()
        out["skipped_size"] = self.skipped_size
        out["conflicts"]    = [vars(c) for c in self.conflicts]
        out["warnings"]     = list(self.warnings)
        return out


# ─────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────

class Cache:
    TTL = 24 * 60 * 60   # seconds

    @staticmethod
    def directory() -> Path:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            return base / "pip-size" / "Cache"
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return base / "pip-size"

    @classmethod
    def _path(cls, url: str) -> Path:
        parsed = urlparse(url)
        raw    = f"{parsed.netloc}{parsed.path}".strip("/")
        safe   = re.sub(r"[^A-Za-z0-9._+-]+", "_", raw)
        return cls.directory() / f"{safe}.json"

    @classmethod
    def read(cls, url: str) -> dict | None:
        path = cls._path(url)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > cls.TTL:
            log.debug("cache expired (%.0fh old) %s", age / 3600, path.name)
            path.unlink(missing_ok=True)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            log.debug("cache hit  %s", path.name)
            return data
        except (json.JSONDecodeError, OSError):
            path.unlink(missing_ok=True)
            return None

    @classmethod
    def write(cls, url: str, data: dict) -> None:
        path = cls._path(url)
        tmp  = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)           # atomic: readers never see a half-written file
            log.debug("cache write  %s", path.name)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            log.debug("cache write failed: %s", e)

    @classmethod
    def clear(cls) -> int:
        """Delete all cached files. Returns the number of files removed."""
        directory = cls.directory()
        if not directory.exists():
            return 0
        count = len(list(directory.glob("*.json")))
        shutil.rmtree(directory, ignore_errors=True)
        return count


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


# ─────────────────────────────────────────────────────────────────────
# Wheel / file selector
# ─────────────────────────────────────────────────────────────────────

class WheelSelector:
    """Picks the best distribution file for the current interpreter and platform."""

    # {tag_str: priority} mirroring pip's preference order
    _SUPPORTED = {str(t): i for i, t in enumerate(sys_tags())}
    _ENV       = default_environment()

    @classmethod
    def best(cls, files: list[dict], allow_yanked: bool = False) -> dict | None:
        compatible: list[tuple[int, int, dict]] = []

        for f in files:
            if f.get("yanked") and not allow_yanked:
                log.debug("skip  %s  (yanked)", f["filename"])
                continue

            requires_python = f.get("requires_python")
            if requires_python:
                try:
                    spec = SpecifierSet(requires_python)
                except Exception:
                    log.debug("skip  %s  (unparseable requires_python %r)", f["filename"], requires_python)
                    continue
                if not spec.contains(cls._ENV["python_version"]):
                    log.debug("skip  %s  (requires_python %s)", f["filename"], requires_python)
                    continue

            if f["filename"].endswith(".whl"):
                priority = cls._wheel_priority(f["filename"])
                if priority < sys.maxsize:
                    compatible.append((0, priority, f))   # 0 = wheel preferred
            else:
                compatible.append((1, 0, f))               # 1 = sdist fallback

        if not compatible:
            return None

        chosen = sorted(compatible, key=lambda x: (x[0], x[1]))[0][2]
        log.debug("best file: %s  (%d bytes)", chosen["filename"], chosen.get("size", 0))
        return chosen

    @classmethod
    def _wheel_priority(cls, filename: str) -> int:
        parts = filename[:-4].split("-")   # strip .whl
        if len(parts) < 5:
            return sys.maxsize

        python_tag, abi_tag, platform_tag = parts[-3], parts[-2], parts[-1]
        best = sys.maxsize
        for py in python_tag.split("."):
            for abi in abi_tag.split("."):
                for plat in platform_tag.split("."):
                    prio = cls._SUPPORTED.get(f"{py}-{abi}-{plat}", sys.maxsize)
                    best = min(best, prio)

        log.debug(
            "wheel priority  %-60s  %s",
            filename,
            best if best < sys.maxsize else "incompatible",
        )
        return best


def pick_release(
    versions: dict[str, list[dict]],
    spec:     SpecifierSet,
) -> tuple[str, dict | None] | None:
    """
    Choose the release pip would install: the highest version that satisfies *spec* and has a
    file usable on this interpreter/platform. Pre-releases are considered only when the
    specifier asks for them or nothing else matches.

    Returns (version, best_file). best_file is None if the version matches but has no
    compatible file. Returns None if no version satisfies the specifier.
    """
    parsed: list[tuple[Version, str]] = []
    for key in versions:
        try:
            parsed.append((Version(key), key))
        except InvalidVersion:
            log.debug("skipping unparseable version %r", key)
    parsed.sort(reverse=True)

    passes   = (True,) if spec.prereleases is True else (False, True)
    fallback = None
    for allow_pre in passes:
        for version, key in parsed:
            if version.is_prerelease and not allow_pre:
                continue
            if not spec.contains(version, prereleases=True):
                continue
            best = WheelSelector.best(versions[key])
            if best is not None:
                return key, best
            if fallback is None:
                fallback = key
    return (fallback, None) if fallback else None


# ─────────────────────────────────────────────────────────────────────
# Dependency resolver  (BFS)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class _WorkItem:
    """One unit of work in the BFS queue."""
    name:      str
    spec:      str
    extras:    set[str] | None
    via_extra: str | None
    parent:    PackageInfo | None
    depth:     int
    local:     LocalWheel | None = None


_SKIP = object()   # sentinel: marker not satisfied, exclude this dep


class DependencyResolver:
    """
    Resolves a package and its transitive dependencies using BFS.
    Each BFS layer is fetched concurrently via PyPIClient.

    Skipped packages (see SkipRules) stay in the tree with their size, are marked,
    and are not expanded further.
    """

    _ENV = default_environment()

    def __init__(
        self,
        client:         PyPIClient,
        all_extras:     bool                    = False,
        extras_filter:  set[str] | None         = None,
        no_deps:        bool                    = False,
        skip:           SkipRules | None        = None,
        progress:       Callable[[str], None] | None = None,
    ) -> None:
        self._client        = client
        self._all_extras    = all_extras
        self._extras_filter = extras_filter   # glob patterns for root-level extras
        self._no_deps       = no_deps
        self._skip          = skip or SkipRules()
        self._progress      = progress
        self._seen:     set[str]               = set()
        self._resolved: dict[str, PackageInfo] = {}
        self._requirements: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
        self._root_error: str | None = None
        self.conflicts: list[Conflict] = []
        self.warnings:  list[str]      = []

    # ── entry points ─────────────────────────────────────────────────

    async def resolve(self, req: Requirement) -> PackageInfo:
        """Resolve a requirement from PyPI. Raises ResolutionError if the root cannot be resolved."""
        root = _WorkItem(
            name      = req.name,
            spec      = str(req.specifier),
            extras    = self._root_extras(req.extras),
            via_extra = None,
            parent    = None,
            depth     = 0,
        )
        return await self._run(root)

    async def resolve_wheel(self, wheel: LocalWheel, extras: set[str] | None = None) -> PackageInfo:
        """Resolve a local wheel: its size and METADATA come from disk, its dependencies from PyPI."""
        root = _WorkItem(
            name      = wheel.name,
            spec      = "",
            extras    = self._root_extras(extras or set()),
            via_extra = None,
            parent    = None,
            depth     = 0,
            local     = wheel,
        )
        return await self._run(root)

    # ── internal ─────────────────────────────────────────────────────

    def _root_extras(self, explicit: set[str] | None) -> set[str] | None:
        # Explicit extras (pkg[test]) merged with --extras glob patterns; patterns that match
        # nothing are ignored later during marker evaluation.
        merged = set(explicit or ()) | set(self._extras_filter or ())
        return merged or None

    async def _run(self, root: _WorkItem) -> PackageInfo:
        layer: list[_WorkItem] = [root]
        while layer:
            results = await asyncio.gather(*[self._process(item) for item in layer])
            layer   = [item for next_layer in results for item in next_layer]

        self._check_conflicts()
        for node in (p for p in self._resolved.values()):
            node.dependencies.sort(key=lambda d: d.name.lower())   # completion order is random; keep output stable
        pkg = self._resolved.get(canonicalize_name(root.name))
        if pkg is None:
            raise ResolutionError(f"Could not resolve {root.name!r}: {self._root_error or 'unknown error'}")
        return pkg

    def _say(self, message: str) -> None:
        log.info("%s", message.strip())
        if self._progress is not None:
            self._progress(message)

    async def _process(self, item: _WorkItem) -> list[_WorkItem]:
        key = canonicalize_name(item.name)
        self._requirements[key].append((item.spec, item.parent.name if item.parent else None))
        if key in self._seen:
            log.debug("skip  %s  (already resolved)", item.name)
            return []
        self._seen.add(key)

        indent = "  " * item.depth

        if item.local is not None:
            wheel = item.local
            pkg = PackageInfo(
                name=wheel.name, version=wheel.version, size=wheel.size,
                filename=wheel.filename, local=True,
            )
            requires: list[str] = wheel.requires_dist
            self._say(f"{indent}  ✓ {pkg.name}=={pkg.version}  →  {pkg.filename}  (local file)")
        else:
            try:
                version, best_file, release = await self._select(item)
            except (RuntimeError, ValueError) as exc:
                self._fail(item, str(exc))
                return []

            reason = self._skip.match(item.name, item.spec) if item.depth > 0 else None
            pkg = PackageInfo(
                name      = item.name,
                version   = version,
                size      = best_file["size"]     if best_file else 0,
                filename  = best_file["filename"] if best_file else "N/A",
                via_extra = item.via_extra,
                skipped   = reason,
            )
            note = f"  (skipped: {reason})" if reason else ""
            self._say(f"{indent}  ✓ {pkg.name}=={pkg.version}  →  {pkg.filename}{note}")

            if reason or self._no_deps:
                self._attach(key, pkg, item)
                return []

            try:
                release = release or await self._client.release(item.name, version)
            except RuntimeError as exc:
                # The package itself resolved; only its dependency list is unknown.
                self.warnings.append(f"{item.name}=={version}: dependencies unavailable ({exc})")
                self._say(f"{indent}  ⚠ {item.name}  (dependencies unavailable: {exc})")
                release = {"requires_dist": []}
            requires = release["requires_dist"]

        self._attach(key, pkg, item)
        if self._no_deps:
            return []
        return self._next_layer(pkg, item, requires)

    def _attach(self, key: str, pkg: PackageInfo, item: _WorkItem) -> None:
        self._resolved[key] = pkg
        if item.parent is not None:
            item.parent.dependencies.append(pkg)

    def _fail(self, item: _WorkItem, reason: str) -> None:
        indent = "  " * item.depth
        if item.depth == 0:
            self._root_error = reason
        else:
            self.warnings.append(f"{item.name}: {reason}")
        self._say(f"{indent}  ✗ {item.name}  (error: {reason})")

    async def _select(self, item: _WorkItem) -> tuple[str, dict | None, dict | None]:
        """Pick (version, best_file, release_data_or_None) for a registry package."""
        spec   = SpecifierSet(item.spec) if item.spec else SpecifierSet()
        pinned = self._exact_pin(spec)

        if pinned is not None:
            # Exact pin: one request gives both the files and requires_dist.
            try:
                release = await self._client.release(item.name, pinned)
            except RuntimeError as exc:
                if "HTTP 404" in str(exc):
                    raise RuntimeError(f"no version matching {item.spec}") from exc
                raise
            return pinned, WheelSelector.best(release["files"], allow_yanked=True), release

        versions = await self._client.project_files(item.name)
        chosen   = pick_release(versions, spec)
        if chosen is None:
            raise RuntimeError(f"no version matching {item.spec or '*'}")
        version, best = chosen
        return version, best, None

    @staticmethod
    def _exact_pin(spec: SpecifierSet) -> str | None:
        specs = list(spec)
        if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
            try:
                return str(Version(specs[0].version))
            except InvalidVersion:
                return None
        return None

    def _check_conflicts(self) -> None:
        """
        Packages are resolved once, against the first requirement seen. Report every other
        requirement that the selected version does not satisfy.
        """
        self.conflicts = []
        for key, requirements in self._requirements.items():
            pkg = self._resolved.get(key)
            if pkg is None or pkg.local:
                continue
            for spec_str, required_by in requirements:
                if not spec_str:
                    continue
                try:
                    ok = SpecifierSet(spec_str).contains(pkg.version, prereleases=True)
                except Exception:                      # noqa: BLE001
                    continue
                if not ok:
                    self.conflicts.append(Conflict(pkg.name, pkg.version, spec_str, required_by))

    def _next_layer(
        self,
        pkg:      PackageInfo,
        item:     _WorkItem,
        requires: list[str],
    ) -> list[_WorkItem]:
        active_extras = item.extras or set()
        next_items: list[_WorkItem] = []

        for req_str in requires:
            try:
                req = Requirement(req_str)
            except Exception as exc:
                log.debug("could not parse requirement %r: %s", req_str, exc)
                continue

            triggered_by = self._evaluate_marker(req, active_extras)
            if triggered_by is _SKIP:
                continue

            next_items.append(_WorkItem(
                name      = req.name,
                spec      = str(req.specifier),
                extras    = set(req.extras) if req.extras else None,
                via_extra = triggered_by,
                parent    = pkg,
                depth     = item.depth + 1,
            ))

        return next_items

    def _evaluate_marker(self, req: Requirement, active_extras: set[str]) -> object:
        """
        Decides whether a dependency should be included, and under which extra (if any).

        Returns:
          None            — no marker, or non-extra marker satisfied by the environment
          "<extra_name>"  — dep is pulled in by this specific extra
          _SKIP sentinel  — dep should be excluded

        Rules
        ─────
        Non-optional dep (no `extra` in marker):
          → include if marker is satisfied by the base environment.

        Optional dep (`extra == "..."` in marker):
          active_extras belong only to THIS package level — they do not propagate down.

          1. Check if any active_extra (exact or glob) satisfies the marker → include.
          2. If none match:
               - WITHOUT --all-extras → skip.
               - WITH    --all-extras → include if platform/python conditions are also
                 met; label with the extra name extracted from the marker.
        """
        if not req.marker:
            return None

        marker_str = str(req.marker)

        # ── non-optional marker ───────────────────────────────────────
        if "extra" not in marker_str:
            if req.marker.evaluate(self._ENV):
                return None
            log.debug("skip  %s  (marker not satisfied: %s)", req.name, req.marker)
            return _SKIP

        # ── optional marker ───────────────────────────────────────────
        # The extra name declared in the marker (e.g. "security").
        marker_extra = self._extract_extra_name(req)

        # Check active_extras with glob matching.
        for pattern in active_extras:
            if marker_extra and fnmatch.fnmatch(marker_extra, pattern):
                # Verify the full marker (including any platform conditions) is met.
                if req.marker.evaluate({**self._ENV, "extra": marker_extra}):
                    return marker_extra

        # No active extra matched.
        if not self._all_extras:
            log.debug("skip  %s  (optional; use --all-extras to include all)", req.name)
            return _SKIP

        # --all-extras: include if non-extra conditions are also satisfied.
        extra_label = marker_extra or "optional"
        if not req.marker.evaluate({**self._ENV, "extra": extra_label}):
            log.debug(
                "skip  %s  (optional extra=%s but platform/version conditions not met)",
                req.name, extra_label,
            )
            return _SKIP

        log.debug("include  %s  (via --all-extras, extra=%s)", req.name, extra_label)
        return extra_label

    @staticmethod
    def _extract_extra_name(req: Requirement) -> str | None:
        """Pull the extra name out of a marker like `extra == "security"`."""
        match = re.search(r'extra\s*==\s*["\']([^"\']+)["\']', str(req.marker))
        return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────────────
# Printer
# ─────────────────────────────────────────────────────────────────────

class Printer:
    """Formats and prints a Resolution."""

    def __init__(self, as_bytes: bool = False) -> None:
        self._fmt = (lambda b: str(b)) if as_bytes else self.format_size

    def print_tree(self, res: Resolution) -> None:
        root = res.root
        print(f"\n  {root.name}=={root.version}  {self._size_str(root)}{self._tags(root)}")
        for i, dep in enumerate(root.dependencies):
            self._print_node(dep, prefix="  ", is_last=(i == len(root.dependencies) - 1))
        self._print_summary(res)
        print()

    def print_quiet(self, res: Resolution) -> None:
        print(self._fmt(res.total_size))

    def print_json(self, res: Resolution) -> None:
        out = self._to_dict(res.root)
        if res.skipped:
            out["skipped_size"]       = self._fmt(res.skipped_size)
            out["skipped_size_bytes"] = res.skipped_size
            out["skipped_count"]      = len(res.skipped)
        if res.conflicts:
            out["conflicts"] = [vars(c) for c in res.conflicts]
        if res.warnings:
            out["warnings"] = list(res.warnings)
        print(json.dumps(out, indent=2))

    def _print_summary(self, res: Resolution) -> None:
        if res.skipped:
            print(f"\n  Total to download : {self._fmt(res.total_size)}")
            print(f"  Skipped           : {self._fmt(res.skipped_size)}  "
                  f"({len(res.skipped)} package(s), not counted)")
        if res.conflicts:
            print("\n  ⚠ Version conflicts (each package is resolved once, against the first requirement seen):")
            for c in res.conflicts:
                who = c.required_by or "the request"
                print(f"    - {c.name}=={c.version} selected, but {who} requires {c.name}{c.requirement}")
        if res.warnings:
            print("\n  ⚠ Warnings:")
            for w in res.warnings:
                print(f"    - {w}")

    def _print_node(self, pkg: PackageInfo, prefix: str, is_last: bool) -> None:
        connector    = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        print(f"{prefix}{connector}{pkg.name}=={pkg.version}  {self._size_str(pkg)}{self._tags(pkg)}")
        for i, dep in enumerate(pkg.dependencies):
            self._print_node(dep, child_prefix, is_last=(i == len(pkg.dependencies) - 1))

    @staticmethod
    def _tags(pkg: PackageInfo) -> str:
        tags = []
        if pkg.via_extra:
            tags.append(f"[extra: {pkg.via_extra}]")
        if pkg.skipped:
            tags.append(f"[skipped: {pkg.skipped}]")
        if pkg.local:
            tags.append("[local wheel]")
        return ("  " + "  ".join(tags)) if tags else ""

    def _size_str(self, pkg: PackageInfo) -> str:
        """Own size, plus total in parentheses for non-leaf nodes (skipped packages excluded)."""
        own = self._fmt(pkg.size)
        if not pkg.dependencies:
            return own
        return f"{own}  (total: {self._fmt(pkg.total_size())})"

    def _to_dict(self, pkg: PackageInfo) -> dict:
        total  = pkg.total_size()
        result: dict = {
            "name":             pkg.name,
            "version":          pkg.version,
            "size":             self._fmt(pkg.size),
            "size_bytes":       pkg.size,
            "total_size":       self._fmt(total),
            "total_size_bytes": total,
            "filename":         pkg.filename,
        }
        if pkg.via_extra:
            result["via_extra"] = pkg.via_extra
        if pkg.skipped:
            result["skipped"] = pkg.skipped
        if pkg.local:
            result["local"] = True
        if pkg.dependencies:
            result["dependencies"] = [self._to_dict(d) for d in pkg.dependencies]
        return result

    @staticmethod
    def format_size(size_bytes: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"
