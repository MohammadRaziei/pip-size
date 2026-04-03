"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    pip-size "requests"
    pip-size "requests" --no-deps
    pip-size "requests==2.31.0"
    pip-size "requests" --include-optional
    pip-size "requests" --verbose
    pip-size "requests" --extra-verbose
    pip-size "requests" --no-cache
    pip-size --clear-cache

Proxy support:
    pip-size "requests" --proxy http://user:pass@host:8080
    pip-size "requests" --proxy socks5://user:pass@host:1080  # requires: pip install aiohttp-socks
    HTTP_PROXY=http://host:8080  pip-size "requests"          # env var fallback
    ALL_PROXY=socks5://host:1080 pip-size "requests"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

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
from packaging.version import Version


log = logging.getLogger("pip_size")

PYPI_JSON_API   = "https://pypi.org/pypi/{package}/json"
MAX_CONCURRENCY = 10


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
    dependencies: list[PackageInfo] = field(default_factory=list)

    def total_size(self) -> int:
        return self.size + sum(d.total_size() for d in self.dependencies)


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
        safe = (
            url.replace("https://pypi.org/pypi/", "")
               .removesuffix("/json")
               .replace("/", "_")
        )
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
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
            log.debug("cache write  %s", path.name)
        except OSError as e:
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

class PyPIClient:
    """
    Async PyPI JSON API client with caching and proxy support.

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
        self.proxy_url = self._resolve_proxy(proxy)
        self.use_cache = use_cache
        self._sem      = asyncio.Semaphore(MAX_CONCURRENCY)
        self._session: aiohttp.ClientSession | None = None

    # ── proxy helpers ────────────────────────────────────────────────

    def _resolve_proxy(self, flag: str | None) -> str | None:
        if flag:
            return flag
        for var in self._ENV_PROXY_VARS:
            val = os.environ.get(var)
            if val:
                return val
        return None

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
            headers   = {"User-Agent": "pip-size/1.0", "Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── public API ───────────────────────────────────────────────────

    async def fetch(self, package_name: str) -> dict:
        """Fetch and return the PyPI JSON data for a package."""
        url = PYPI_JSON_API.format(package=package_name)

        if self.use_cache:
            cached = Cache.read(url)
            if cached is not None:
                return cached

        log.debug("GET %s%s", url, f"  (proxy: {self.proxy_url})" if self.proxy_url else "")

        assert self._session is not None, "PyPIClient must be used as an async context manager"
        async with self._sem:
            try:
                async with self._session.get(url, proxy=self._request_proxy) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}: {resp.reason}  ({url})")
                    data = await resp.json(content_type=None)
                    log.debug("response OK  (%s)", url)
                    if self.use_cache:
                        Cache.write(url, data)
                    return data
            except aiohttp.ClientError as exc:
                raise RuntimeError(f"Network error: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────
# Wheel / file selector
# ─────────────────────────────────────────────────────────────────────

class WheelSelector:
    """Picks the best distribution file for the current interpreter and platform."""

    # {tag_str: priority} mirroring pip's preference order
    _SUPPORTED = {str(t): i for i, t in enumerate(sys_tags())}
    _ENV       = default_environment()

    @classmethod
    def best(cls, files: list[dict]) -> dict | None:
        compatible: list[tuple[int, int, dict]] = []

        for f in files:
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


_SKIP = object()   # sentinel: marker not satisfied, exclude this dep


class DependencyResolver:
    """
    Resolves a package and its transitive dependencies using BFS.
    Each BFS layer is fetched concurrently via PyPIClient.
    """

    _ENV = default_environment()

    def __init__(
        self,
        client:           PyPIClient,
        include_optional: bool = False,
        no_deps:          bool = False,
        quiet:            bool = False,
    ) -> None:
        self._client           = client
        self._include_optional = include_optional
        self._no_deps          = no_deps
        self._quiet            = quiet
        self._seen:     set[str]               = set()
        self._resolved: dict[str, PackageInfo] = {}

    async def resolve(self, req: Requirement) -> PackageInfo | None:
        root = _WorkItem(
            name      = req.name,
            spec      = str(req.specifier),
            extras    = set(req.extras) if req.extras else None,
            via_extra = None,
            parent    = None,
            depth     = 0,
        )
        layer: list[_WorkItem] = [root]
        while layer:
            results = await asyncio.gather(*[self._process(item) for item in layer])
            layer   = [item for next_layer in results for item in next_layer]
        return self._resolved.get(req.name.lower())

    # ── internal ─────────────────────────────────────────────────────

    async def _process(self, item: _WorkItem) -> list[_WorkItem]:
        key = item.name.lower()
        if key in self._seen:
            log.debug("skip  %s  (already resolved)", item.name)
            return []
        self._seen.add(key)

        data = await self._fetch(item)
        if data is None:
            return []

        pkg = self._build_package(item, data)
        if pkg is None:
            return []

        self._resolved[key] = pkg
        if item.parent is not None:
            item.parent.dependencies.append(pkg)

        if self._no_deps:
            return []

        return self._next_layer(pkg, item, data)

    async def _fetch(self, item: _WorkItem) -> dict | None:
        indent = "  " * item.depth
        try:
            return await self._client.fetch(item.name)
        except RuntimeError as exc:
            log.warning("%s✗ %s  (%s)", indent, item.name, exc)
            if not self._quiet:
                print(f"{indent}  ✗ {item.name}  (error: {exc})")
            return None

    def _build_package(self, item: _WorkItem, data: dict) -> PackageInfo | None:
        indent   = "  " * item.depth
        releases = data.get("releases", {})
        resolved = self._pick_version(item, data)

        if resolved is None:
            log.warning("%s✗ %s  (no version matching %s)", indent, item.name, item.spec)
            if not self._quiet:
                print(f"{indent}  ✗ {item.name}  (no version matching {item.spec})")
            return None

        best_file = WheelSelector.best(releases.get(resolved, []))
        size      = best_file["size"]     if best_file else 0
        filename  = best_file["filename"] if best_file else "N/A"

        log.info(
            "%s✓ %s==%s  →  %s  (%s)",
            indent, item.name, resolved, filename, Printer.format_size(size),
        )
        if not self._quiet:
            print(f"{indent}  ✓ {item.name}=={resolved}  →  {filename}")

        return PackageInfo(
            name      = item.name,
            version   = resolved,
            size      = size,
            filename  = filename,
            via_extra = item.via_extra,
        )

    def _pick_version(self, item: _WorkItem, data: dict) -> str | None:
        latest   = data["info"]["version"]
        releases = data.get("releases", {})

        if not item.spec:
            return latest

        specifier = SpecifierSet(item.spec)
        try:
            if specifier.contains(latest):
                return latest
        except Exception:
            log.debug("latest version %r is unparseable, scanning releases", latest)

        return self._best_stable_version(releases, item.spec)

    @staticmethod
    def _best_stable_version(releases: dict, spec_str: str) -> str | None:
        spec       = SpecifierSet(spec_str, prereleases=False)
        candidates = []
        for v_str in releases:
            try:
                v = Version(v_str)
            except Exception:
                log.debug("skipping unparseable version %r", v_str)
                continue
            if not v.is_prerelease:
                candidates.append(v)
        for v in sorted(candidates, reverse=True):
            if v in spec:
                return str(v)
        return None

    def _next_layer(
        self,
        pkg:  PackageInfo,
        item: _WorkItem,
        data: dict,
    ) -> list[_WorkItem]:
        active_extras = item.extras or set()
        next_items: list[_WorkItem] = []

        for req_str in (data["info"].get("requires_dist") or []):
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
        Non-optional dep (marker has no `extra` condition):
          → include only if the marker is satisfied by the base environment.

        Optional dep (marker contains `extra == "..."`):
          The `active_extras` only represent the extras explicitly requested FOR THIS
          PACKAGE (from its parent's requires_dist entry, e.g. `fastapi[standard]`).
          They do NOT propagate to grandchildren.

          → Check each active_extra. If the full marker (env + extra) is satisfied
            → include, labelled with that extra name.
          → If none of the active_extras satisfy it:
              - WITHOUT --include-optional → skip.
              - WITH    --include-optional → include anyway, labelled with the extra
                name extracted from the marker, but ONLY if the non-extra part of
                the marker (platform, python_version, etc.) is also satisfied.
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
        # Check against each explicitly-requested extra for this package.
        for extra in active_extras:
            if req.marker.evaluate({**self._ENV, "extra": extra}):
                return extra

        # No active extra satisfies it.
        if not self._include_optional:
            log.debug("skip  %s  (optional; use --include-optional to include all)", req.name)
            return _SKIP

        # --include-optional: include if the non-extra conditions are met.
        extra_label = self._extract_extra_name(req) or "optional"
        # Verify the rest of the marker (python_version, platform, etc.) is satisfied
        # by evaluating with the extracted extra injected into the environment.
        if not req.marker.evaluate({**self._ENV, "extra": extra_label}):
            log.debug(
                "skip  %s  (optional extra=%s but platform/version conditions not met)",
                req.name, extra_label,
            )
            return _SKIP

        log.debug("include  %s  (optional via --include-optional, extra=%s)", req.name, extra_label)
        return extra_label

    @staticmethod
    def _extract_extra_name(req: Requirement) -> str | None:
        """Pull the extra name out of a marker like `extra == "security"`."""
        import re
        match = re.search(r'extra\s*==\s*["\']([^"\']+)["\']', str(req.marker))
        return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────────────
# Printer
# ─────────────────────────────────────────────────────────────────────

class Printer:
    """Formats and prints a resolved PackageInfo tree."""

    def __init__(self, as_bytes: bool = False) -> None:
        self._fmt = (lambda b: str(b)) if as_bytes else self.format_size

    def print_tree(self, pkg: PackageInfo) -> None:
        total = pkg.total_size()
        print(f"\n  {pkg.name}=={pkg.version}  ({self._fmt(total)} total)")
        for i, dep in enumerate(pkg.dependencies):
            self._print_node(dep, prefix="  ", is_last=(i == len(pkg.dependencies) - 1))
        print()

    def print_quiet(self, pkg: PackageInfo) -> None:
        print(self._fmt(pkg.total_size()))

    def print_json(self, pkg: PackageInfo) -> None:
        print(json.dumps(self._to_dict(pkg), indent=2))

    def _print_node(self, pkg: PackageInfo, prefix: str, is_last: bool) -> None:
        connector    = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        extra_tag    = f"  [extra: {pkg.via_extra}]" if pkg.via_extra else ""
        print(f"{prefix}{connector}{pkg.name}=={pkg.version}  {self._fmt(pkg.size)}{extra_tag}")
        for i, dep in enumerate(pkg.dependencies):
            self._print_node(dep, child_prefix, is_last=(i == len(pkg.dependencies) - 1))

    def _to_dict(self, pkg: PackageInfo) -> dict:
        total  = pkg.total_size()
        result: dict = {
            "name":       pkg.name,
            "version":    pkg.version,
            "size":       self._fmt(pkg.size),
            "total_size": self._fmt(total),
            "filename":   pkg.filename,
        }
        if pkg.via_extra:
            result["via_extra"] = pkg.via_extra
        if pkg.dependencies:
            result["dependencies"] = [self._to_dict(d) for d in pkg.dependencies]
        return result

    @staticmethod
    def format_size(size_bytes: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes //= 1024
        return f"{size_bytes:.1f} TB"


# ─────────────────────────────────────────────────────────────────────
# PipSize  — top-level orchestrator
# ─────────────────────────────────────────────────────────────────────

class PipSize:
    """
    Orchestrates fetching, resolving, and printing a package's download size.

    Sync usage:
        PipSize(no_deps=True).run("requests==2.31.0")

    Async usage:
        await PipSize().run_async("requests")
    """

    def __init__(
        self,
        no_deps:          bool       = False,
        include_optional: bool       = False,
        use_cache:        bool       = True,
        proxy:            str | None = None,
        quiet:            bool       = False,
        as_bytes:         bool       = False,
        as_json:          bool       = False,
    ) -> None:
        self.no_deps          = no_deps
        self.include_optional = include_optional
        self.use_cache        = use_cache
        self.proxy            = proxy
        self.quiet            = quiet
        self.as_bytes         = as_bytes
        self.as_json          = as_json

    def run(self, package_spec: str) -> None:
        asyncio.run(self.run_async(package_spec))

    async def run_async(self, package_spec: str) -> None:
        req     = Requirement(package_spec)
        client  = PyPIClient(proxy=self.proxy, use_cache=self.use_cache)
        printer = Printer(as_bytes=self.as_bytes)

        self._print_header(package_spec, client)

        log.info(
            "starting  package=%s  spec=%s  no_deps=%s  optional=%s  cache=%s  proxy=%s",
            req.name, str(req.specifier) or "latest",
            self.no_deps, self.include_optional, self.use_cache,
            client.proxy_url or "none",
        )
        log.debug("cache dir: %s", Cache.directory())
        log.debug("platform tags (top 5): %s", list(WheelSelector._SUPPORTED.keys())[:5])

        async with client:
            resolver = DependencyResolver(
                client           = client,
                include_optional = self.include_optional,
                no_deps          = self.no_deps,
                quiet            = self.quiet,
            )
            pkg = await resolver.resolve(req)

        if pkg is None:
            if not self.quiet:
                print("\n❌ Could not resolve package.")
            return

        log.info("resolved  total=%s", Printer.format_size(pkg.total_size()))

        if self.as_json:
            printer.print_json(pkg)
        elif self.quiet:
            printer.print_quiet(pkg)
        else:
            printer.print_tree(pkg)

    def _print_header(self, package_spec: str, client: PyPIClient) -> None:
        if self.quiet:
            return
        notes = []
        if not self.use_cache:
            notes.append("cache disabled")
        if self.include_optional:
            notes.append("including optional deps")
        if client.proxy_url:
            notes.append(f"proxy: {client.proxy_url}")
        suffix = f"  ({', '.join(notes)})" if notes else ""
        print(f"\n🔍 Resolving '{package_spec}'...{suffix}")


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Calculate real download size of a PyPI package. Zero downloads."
    )
    parser.add_argument(
        "package",
        nargs="?",
        help='e.g. "requests" or "requests==2.31.0"',
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Show size of the package itself only, without resolving dependencies.",
    )
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help=(
            "Include optional dependencies (those gated behind an `extra` marker). "
            "By default these are skipped unless you requested that extra explicitly."
        ),
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        default=None,
        help=(
            "Proxy URL for all PyPI requests (overrides HTTP_PROXY / ALL_PROXY env vars).\n"
            "  HTTP:   --proxy http://user:pass@host:8080\n"
            "  SOCKS5: --proxy socks5://user:pass@host:1080  (requires: pip install aiohttp-socks)\n"
            "  SOCKS4: --proxy socks4://host:1080            (requires: pip install aiohttp-socks)"
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache and always fetch fresh data from PyPI.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=f"Delete all cached PyPI responses and exit.  (cache dir: {Cache.directory()})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the total size and nothing else.",
    )
    parser.add_argument(
        "--bytes",
        action="store_true",
        help="Report all sizes in raw bytes instead of human-readable units.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the full dependency tree as JSON.",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help="Enable INFO logging.",
    )
    verbosity.add_argument(
        "--extra-verbose",
        action="store_true",
        help="Enable DEBUG logging (HTTP, wheel scoring, marker evaluation, cache).",
    )

    args = parser.parse_args()

    if args.extra_verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)-8s  %(name)s  %(message)s")
    elif args.verbose:
        logging.basicConfig(level=logging.INFO,  format="%(levelname)-8s  %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.clear_cache:
        removed = Cache.clear()
        print(f"🗑  Cache cleared — {removed} file(s) removed.  ({Cache.directory()})")
        sys.exit(0)

    if not args.package:
        parser.error("the following arguments are required: package")

    try:
        PipSize(
            no_deps          = args.no_deps,
            include_optional = args.include_optional,
            use_cache        = not args.no_cache,
            proxy            = args.proxy,
            quiet            = args.quiet,
            as_bytes         = args.bytes,
            as_json          = args.json,
        ).run(args.package)
    except RuntimeError as exc:
        log.error("%s", exc)
        print(f"\n❌ Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()