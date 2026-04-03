"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    pip-size "requests"
    pip-size "requests" --no-deps
    pip-size "requests==2.31.0"
    pip-size "requests" --optional-deps
    pip-size "requests" --verbose
    pip-size "requests" --extra-verbose
    pip-size "requests" --no-cache
    pip-size --clear-cache

Proxy support:
    # HTTP proxy via flag
    pip-size "requests" --proxy http://user:pass@host:8080

    # SOCKS5 proxy via flag  (requires: pip install aiohttp-socks)
    pip-size "requests" --proxy socks5://user:pass@host:1080

    # Via environment variables (flag takes precedence)
    HTTP_PROXY=http://host:8080  pip-size "requests"
    ALL_PROXY=socks5://host:1080 pip-size "requests"
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

# aiohttp-socks is optional — only needed for socks4/socks5 proxies
try:
    from aiohttp_socks import ProxyConnector as _SocksProxyConnector
    _SOCKS_AVAILABLE = True
except ImportError:
    _SocksProxyConnector = None  # type: ignore[assignment,misc]
    _SOCKS_AVAILABLE = False

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.version import Version


log = logging.getLogger("pip_size")

PYPI_JSON_API     = "https://pypi.org/pypi/{package}/json"
CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_CONCURRENCY   = 10   # max simultaneous PyPI requests

# ordered dict of {tag_str: priority} for this interpreter/platform, mirroring pip's preference
SUPPORTED_TAGS = {str(t): i for i, t in enumerate(sys_tags())}


# ───────────────────────────── cache ────────────────────────────────


def _cache_dir() -> Path:
    """
    Return the platform-appropriate cache directory:
      Linux / macOS : ~/.cache/pip-size  (respects $XDG_CACHE_HOME)
      Windows       : %LOCALAPPDATA%/pip-size/Cache
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "pip-size" / "Cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "pip-size"


def _cache_path(url: str) -> Path:
    safe = url.replace("https://pypi.org/pypi/", "").removesuffix("/json").replace("/", "_")
    return _cache_dir() / f"{safe}.json"


def _cache_read(url: str) -> dict | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > CACHE_TTL_SECONDS:
        log.debug("cache expired  (%.0fh old)  %s", age / 3600, path.name)
        path.unlink(missing_ok=True)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        log.debug("cache hit  %s", path.name)
        return data
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return None


def _cache_write(url: str, data: dict) -> None:
    path = _cache_path(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        log.debug("cache write  %s", path.name)
    except OSError as e:
        log.debug("cache write failed: %s", e)


def clear_cache() -> int:
    """Delete all cached files. Returns the number of files removed."""
    cache = _cache_dir()
    if not cache.exists():
        return 0
    count = len(list(cache.glob("*.json")))
    shutil.rmtree(cache, ignore_errors=True)
    return count


# ───────────────────────────── async http ───────────────────────────


async def _fetch_json_async(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
    use_cache: bool = True,
    proxy: str | None = None,
) -> dict:
    if use_cache:
        cached = _cache_read(url)
        if cached is not None:
            return cached

    log.debug("GET %s%s", url, f"  (proxy: {proxy})" if proxy else "")
    async with sem:
        try:
            async with session.get(url, proxy=proxy) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}: {resp.reason}  ({url})")
                data = await resp.json(content_type=None)
                log.debug("response OK  (%s)", url)
                if use_cache:
                    _cache_write(url, data)
                return data
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Network error: {e}") from e


# ───────────────────────────── wheel selection ──────────────────────


def _parse_wheel_tag(filename: str) -> tuple[str, str, str] | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return parts[-3], parts[-2], parts[-1]


def _wheel_priority(filename: str) -> int:
    tags = _parse_wheel_tag(filename)
    if tags is None:
        return sys.maxsize

    python_tag, abi_tag, platform_tag = tags
    best = sys.maxsize
    for py in python_tag.split("."):
        for abi in abi_tag.split("."):
            for plat in platform_tag.split("."):
                key = f"{py}-{abi}-{plat}"
                if key in SUPPORTED_TAGS:
                    best = min(best, SUPPORTED_TAGS[key])

    log.debug("wheel priority  %-60s  %s", filename, best if best < sys.maxsize else "incompatible")
    return best


def _best_file(files: list[dict]) -> dict | None:
    env = default_environment()
    compatible = []
    for f in files:
        requires_python = f.get("requires_python")
        if requires_python:
            if not SpecifierSet(requires_python).contains(env["python_version"]):
                log.debug("skip  %s  (requires_python %s)", f["filename"], requires_python)
                continue

        if f["filename"].endswith(".whl"):
            priority = _wheel_priority(f["filename"])
            if priority < sys.maxsize:
                compatible.append((0, priority, f))
        else:
            compatible.append((1, 0, f))

    if not compatible:
        return None

    compatible.sort(key=lambda x: (x[0], x[1]))
    chosen = compatible[0][2]
    log.debug("best file: %s  (%d bytes)", chosen["filename"], chosen.get("size", 0))
    return chosen


# ───────────────────────────── data model ───────────────────────────


@dataclass
class PackageInfo:
    name:         str
    version:      str
    size:         int
    filename:     str
    via_extra:    str | None = None
    dependencies: list["PackageInfo"] = field(default_factory=list)


def _resolve_version(releases: dict, specifier_str: str) -> str | None:
    spec     = SpecifierSet(specifier_str, prereleases=False)
    versions = sorted(
        (Version(v) for v in releases if not Version(v).is_prerelease),
        reverse=True,
    )
    for v in versions:
        if v in spec:
            return str(v)
    return None


# ───────────────────────────── proxy ────────────────────────────────


def _resolve_proxy(proxy_flag: str | None) -> str | None:
    """
    Return the proxy URL to use, with this priority:
      1. --proxy flag
      2. HTTPS_PROXY / HTTP_PROXY env var  (for HTTP proxies)
      3. ALL_PROXY / SOCKS_PROXY env var   (catches socks:// schemes too)
    Returns None if no proxy is configured.
    """
    if proxy_flag:
        return proxy_flag
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "SOCKS_PROXY",
                "https_proxy", "http_proxy", "all_proxy", "socks_proxy"):
        val = os.environ.get(var)
        if val:
            return val
    return None


def _make_connector(proxy_url: str | None, limit: int) -> aiohttp.BaseConnector:
    """
    Build the appropriate aiohttp connector for the given proxy URL.

    - No proxy              → plain TCPConnector
    - http:// / https://    → plain TCPConnector  (proxy passed per-request)
    - socks4:// / socks5:// → SocksProxyConnector (requires aiohttp-socks)
    """
    if proxy_url is None:
        return aiohttp.TCPConnector(limit=limit)

    scheme = proxy_url.split("://")[0].lower()
    if scheme in ("socks4", "socks5", "socks5h"):
        if not _SOCKS_AVAILABLE:
            raise RuntimeError(
                f"SOCKS proxy requested ({proxy_url!r}) but 'aiohttp-socks' is not installed.\n"
                "  Fix: pip install aiohttp-socks"
            )
        log.debug("using SOCKS connector  url=%s", proxy_url)
        return _SocksProxyConnector.from_url(proxy_url, limit=limit)

    # http / https proxy — use plain connector; proxy is passed per-request
    log.debug("using HTTP proxy  url=%s", proxy_url)
    return aiohttp.TCPConnector(limit=limit)


# ───────────────────────────── BFS resolver ─────────────────────────


@dataclass
class _QueueItem:
    """One unit of work in the BFS queue."""
    name:      str
    spec:      str
    extras:    set[str] | None
    via_extra: str | None
    parent:    PackageInfo | None   # None for the root
    depth:     int


async def _resolve_bfs(
    root_req:         Requirement,
    no_deps:          bool,
    include_optional: bool,
    use_cache:        bool,
    quiet:            bool,
    proxy_url:        str | None = None,
) -> PackageInfo | None:
    """
    BFS over the dependency graph.

    Layers are processed level-by-level; within each layer all PyPI fetches
    run concurrently (bounded by MAX_CONCURRENCY).

    Optional dependencies (those only reachable via an `extra` marker and
    not explicitly requested) are skipped unless --optional-deps is set.
    """
    env       = default_environment()
    sem       = asyncio.Semaphore(MAX_CONCURRENCY)

    connector = _make_connector(proxy_url, limit=MAX_CONCURRENCY)
    headers   = {"User-Agent": "pip-size/1.0", "Accept": "application/json"}

    # For SOCKS connectors the proxy is baked into the connector itself;
    # for HTTP proxies we pass it as a per-request kwarg.
    scheme         = (proxy_url or "").split("://")[0].lower()
    is_socks_proxy = scheme in ("socks4", "socks5", "socks5h")
    request_proxy  = None if (proxy_url is None or is_socks_proxy) else proxy_url

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:

        seen:        set[str]                       = set()
        # pkg_node[name] = PackageInfo once resolved
        pkg_node:    dict[str, PackageInfo]         = {}

        # ── helpers ──────────────────────────────────────────────────

        async def fetch_and_resolve(item: _QueueItem) -> list[_QueueItem]:
            """
            Fetch package data, build a PackageInfo, attach it to its parent,
            and return the next-level queue items (its own deps).
            """
            key = item.name.lower()
            if key in seen:
                log.debug("skip  %s  (already resolved)", item.name)
                return []
            seen.add(key)

            indent = "  " * item.depth
            url    = PYPI_JSON_API.format(package=item.name)

            try:
                data = await _fetch_json_async(session, url, sem, use_cache, proxy=request_proxy)
            except RuntimeError as e:
                log.warning("%s✗ %s  (%s)", indent, item.name, e)
                if not quiet:
                    print(f"{indent}  ✗ {item.name}  (error: {e})")
                return []

            releases       = data.get("releases", {})
            latest_version = data["info"]["version"]

            # pick version
            if item.spec:
                specifier = SpecifierSet(item.spec)
                if specifier.contains(latest_version):
                    resolved = latest_version
                else:
                    resolved = _resolve_version(releases, item.spec)
                    if resolved is None:
                        log.warning("%s✗ %s  (no version matching %s)", indent, item.name, item.spec)
                        if not quiet:
                            print(f"{indent}  ✗ {item.name}  (no version matching {item.spec})")
                        return []
            else:
                resolved = latest_version

            files     = releases.get(resolved, [])
            best_file = _best_file(files)
            size      = best_file["size"]     if best_file else 0
            filename  = best_file["filename"] if best_file else "N/A"

            log.info("%s✓ %s==%s  →  %s  (%s)", indent, item.name, resolved, filename, _format_size(size))
            if not quiet:
                print(f"{indent}  ✓ {item.name}=={resolved}  →  {filename}")

            pkg            = PackageInfo(name=item.name, version=resolved, size=size, filename=filename, via_extra=item.via_extra)
            pkg_node[key]  = pkg

            # attach to parent
            if item.parent is not None:
                item.parent.dependencies.append(pkg)

            if no_deps:
                return []

            # ── enumerate next-level deps ─────────────────────────────
            active_extras = item.extras or set()
            requires_dist = data["info"].get("requires_dist") or []
            next_items:   list[_QueueItem] = []

            for req_str in requires_dist:
                try:
                    req = Requirement(req_str)
                except Exception as e:
                    log.debug("could not parse requirement %r: %s", req_str, e)
                    continue

                triggered_by: str | None = None

                if req.marker:
                    # Check whether this dep is *only* reachable via an extra
                    # (i.e. the marker contains `extra == "..."`)
                    marker_str          = str(req.marker)
                    is_optional_only    = 'extra' in marker_str

                    if is_optional_only and not include_optional:
                        # only evaluate against explicitly requested extras
                        for extra in (active_extras or set()):
                            marker_env = {**env, "extra": extra}
                            if req.marker.evaluate(marker_env):
                                triggered_by = extra
                                break
                        if triggered_by is None:
                            log.debug(
                                "skip  %s  (optional dep, use --optional-deps to include: %s)",
                                req.name, req.marker,
                            )
                            continue
                    else:
                        # evaluate normally (no-extra env first, then per-extra)
                        if req.marker.evaluate(env):
                            triggered_by = None   # non-extra marker, satisfied
                        else:
                            for extra in (active_extras or set()):
                                marker_env = {**env, "extra": extra}
                                if req.marker.evaluate(marker_env):
                                    triggered_by = extra
                                    break
                            else:
                                log.debug(
                                    "skip  %s  (marker not satisfied for extras=%s: %s)",
                                    req.name, active_extras, req.marker,
                                )
                                continue

                next_items.append(_QueueItem(
                    name      = req.name,
                    spec      = str(req.specifier),
                    extras    = set(req.extras) if req.extras else None,
                    via_extra = triggered_by,
                    parent    = pkg,
                    depth     = item.depth + 1,
                ))

            return next_items

        # ── BFS loop ─────────────────────────────────────────────────

        root_item = _QueueItem(
            name      = root_req.name,
            spec      = str(root_req.specifier),
            extras    = set(root_req.extras) if root_req.extras else None,
            via_extra = None,
            parent    = None,
            depth     = 0,
        )

        current_layer: list[_QueueItem] = [root_item]

        while current_layer:
            # run all items in this layer concurrently
            results = await asyncio.gather(
                *[fetch_and_resolve(item) for item in current_layer],
                return_exceptions=False,
            )
            # flatten next-layer items
            next_layer: list[_QueueItem] = []
            for next_items in results:
                next_layer.extend(next_items)
            current_layer = next_layer

        root_key = root_req.name.lower()
        return pkg_node.get(root_key)


# ───────────────────────────── display ──────────────────────────────


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _total_size(pkg: PackageInfo) -> int:
    return pkg.size + sum(_total_size(d) for d in pkg.dependencies)


def _to_dict(pkg: PackageInfo, as_bytes: bool = False) -> dict:
    total  = _total_size(pkg)
    result = {
        "name":       pkg.name,
        "version":    pkg.version,
        "size":       pkg.size if as_bytes else _format_size(pkg.size),
        "total_size": total    if as_bytes else _format_size(total),
        "filename":   pkg.filename,
    }
    if pkg.via_extra:
        result["via_extra"] = pkg.via_extra
    if pkg.dependencies:
        result["dependencies"] = [_to_dict(d, as_bytes) for d in pkg.dependencies]
    return result


def _print_tree(
    pkg:     PackageInfo,
    prefix:  str  = "",
    is_last: bool = True,
    is_root: bool = False,
    fmt           = _format_size,
) -> None:
    if is_root:
        total = _total_size(pkg)
        print(f"\n  {pkg.name}=={pkg.version}  ({fmt(total)} total)")
        child_prefix = "  "
    else:
        connector    = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        extra_tag    = f"  [extra: {pkg.via_extra}]" if pkg.via_extra else ""
        print(f"{prefix}{connector}{pkg.name}=={pkg.version}  {fmt(pkg.size)}{extra_tag}")

    for i, dep in enumerate(pkg.dependencies):
        _print_tree(
            dep,
            prefix  = child_prefix,
            is_last = i == len(pkg.dependencies) - 1,
            fmt     = fmt,
        )


# ───────────────────────────── entry point ──────────────────────────


async def pip_size_async(
    package_spec:     str,
    no_deps:          bool = False,
    include_optional: bool = False,
    use_cache:        bool = True,
    quiet:            bool = False,
    as_bytes:         bool = False,
    as_json:          bool = False,
    proxy:            str | None = None,
) -> None:
    req = Requirement(package_spec)

    proxy_url = _resolve_proxy(proxy)

    log.info(
        "starting  package=%s  spec=%s  no_deps=%s  optional=%s  cache=%s  proxy=%s",
        req.name, str(req.specifier) or "latest", no_deps, include_optional, use_cache,
        proxy_url or "none",
    )
    log.debug("cache dir: %s", _cache_dir())
    log.debug("platform tags (top 5): %s", list(SUPPORTED_TAGS.keys())[:5])

    if not quiet:
        cache_note    = "  (cache disabled)" if not use_cache else ""
        optional_note = "  (including optional deps)" if include_optional else ""
        proxy_note    = f"  (proxy: {proxy_url})" if proxy_url else ""
        print(f"\n🔍 Resolving '{package_spec}'...{cache_note}{optional_note}{proxy_note}")

    pkg = await _resolve_bfs(
        root_req         = req,
        no_deps          = no_deps,
        include_optional = include_optional,
        use_cache        = use_cache,
        quiet            = quiet,
        proxy_url        = proxy_url,
    )

    if pkg is None:
        if not quiet:
            print("\n❌ Could not resolve package.")
        return

    total = _total_size(pkg)
    log.info("resolved  total=%s", _format_size(total))

    if as_json:
        print(json.dumps(_to_dict(pkg, as_bytes), indent=2))
    elif quiet:
        print(total if as_bytes else _format_size(total))
    else:
        fmt = (lambda b: str(b)) if as_bytes else _format_size
        _print_tree(pkg, is_root=True, fmt=fmt)
        print()


def pip_size(
    package_spec:     str,
    no_deps:          bool = False,
    include_optional: bool = False,
    use_cache:        bool = True,
    quiet:            bool = False,
    as_bytes:         bool = False,
    as_json:          bool = False,
    proxy:            str | None = None,
) -> None:
    asyncio.run(pip_size_async(
        package_spec     = package_spec,
        no_deps          = no_deps,
        include_optional = include_optional,
        use_cache        = use_cache,
        quiet            = quiet,
        as_bytes         = as_bytes,
        as_json          = as_json,
        proxy            = proxy,
    ))


# ────────────────────────────────────────────────────────────────────


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
        "--optional-deps",
        action="store_true",
        help=(
            "Include optional dependencies (those gated behind an `extra` marker). "
            "By default these are skipped unless you requested that extra explicitly."
        ),
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
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache and always fetch fresh data from PyPI.",
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        default=None,
        help=(
            "Proxy URL to use for all PyPI requests.  Overrides HTTP_PROXY / ALL_PROXY env vars.\n"
            "  HTTP:   --proxy http://user:pass@host:8080\n"
            "  HTTPS:  --proxy https://host:8080\n"
            "  SOCKS5: --proxy socks5://user:pass@host:1080  (requires: pip install aiohttp-socks)\n"
            "  SOCKS4: --proxy socks4://host:1080            (requires: pip install aiohttp-socks)"
        ),
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=f"Delete all cached PyPI responses and exit.  (cache dir: {_cache_dir()})",
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
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)-8s  %(name)s  %(message)s",
        )
    elif args.verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)-8s  %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.clear_cache:
        removed = clear_cache()
        print(f"🗑  Cache cleared — {removed} file(s) removed.  ({_cache_dir()})")
        sys.exit(0)

    if not args.package:
        parser.error("the following arguments are required: package")

    try:
        pip_size(
            args.package,
            no_deps          = args.no_deps,
            include_optional = args.optional_deps,
            use_cache        = not args.no_cache,
            quiet            = args.quiet,
            as_bytes         = args.bytes,
            as_json          = args.json,
            proxy            = args.proxy,
        )
    except RuntimeError as e:
        log.error("%s", e)
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()