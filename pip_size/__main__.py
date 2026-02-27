"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    python pip_size.py requests
    python pip_size.py requests --solo
    python pip_size.py "requests==2.31.0"
    python pip_size.py requests --verbose
    python pip_size.py requests --extra-verbose
    python pip_size.py requests --no-cache
    python pip_size.py --clear-cache
"""

import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.version import Version


log = logging.getLogger("pip_size")

PYPI_JSON_API     = "https://pypi.org/pypi/{package}/json"
PYPI_VER_API      = "https://pypi.org/pypi/{package}/{version}/json"
CACHE_TTL_SECONDS = 24 * 60 * 60

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
    safe = url.replace("https://pypi.org/pypi/", "").replace("/", "_")
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


# ───────────────────────────── http ─────────────────────────────────


def _fetch_json(url: str, use_cache: bool = True) -> dict:
    if use_cache:
        cached = _cache_read(url)
        if cached is not None:
            return cached

    log.debug("GET %s", url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pip-size/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read().decode())
            log.debug("response OK  (%s)", url)
            if use_cache:
                _cache_write(url, data)
            return data
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}  ({url})") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e


# ───────────────────────────── wheel selection ──────────────────────


def _parse_wheel_tag(filename: str) -> tuple[str, str, str] | None:
    """
    Extract (python_tag, abi_tag, platform_tag) from a wheel filename.
    Wheel format: {name}-{ver}(-{build})?-{python}-{abi}-{platform}.whl
    """
    if not filename.endswith(".whl"):
        return None
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return None
    return parts[-3], parts[-2], parts[-1]


def _wheel_priority(filename: str) -> int:
    """
    Return the priority of a wheel for this platform.
    Lower = better (follows sys_tags ordering).
    Returns sys.maxsize if the wheel is not compatible.
    """
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
    """
    Pick the best compatible file for this platform.
    Prefers wheels over sdist, picks the highest-priority wheel.
    """
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


# ───────────────────────────── pypi api ─────────────────────────────


@dataclass
class PackageInfo:
    name:         str
    version:      str
    size:         int
    filename:     str
    dependencies: list["PackageInfo"] = field(default_factory=list)


def _resolve_version(releases: dict, specifier_str: str) -> str | None:
    """Pick the latest stable version matching the specifier."""
    spec     = SpecifierSet(specifier_str, prereleases=False)
    versions = sorted(
        (Version(v) for v in releases if not Version(v).is_prerelease),
        reverse=True,
    )
    for v in versions:
        if v in spec:
            return str(v)
    return None


def fetch_package(name: str, use_cache: bool = True) -> tuple[dict, str]:
    """Fetch PyPI JSON (latest) and return (data, latest_version). Always exactly one request per package."""
    data = _fetch_json(PYPI_JSON_API.format(package=name), use_cache)
    return data, data["info"]["version"]


def get_package_info(
    name:      str,
    version:   str | None = None,
    spec:      str        = "",
    extras:    set[str]   | None = None,
    seen:      set | None = None,
    solo:      bool       = False,
    use_cache: bool       = True,
    depth:     int        = 0,
) -> PackageInfo | None:
    """
    Recursively resolve a package and its dependencies.
    Uses PyPI JSON API only — zero downloads. Always exactly one request per package.
    extras: set of active extras (e.g. {"dev", "security"}) — used for marker evaluation.
    """
    if seen is None:
        seen = set()

    indent = "  " * depth
    log.info("%sresolving  %s%s", indent, name, f"  (spec: {spec})" if spec else "")

    try:
        data, latest_version = fetch_package(name, use_cache)
    except RuntimeError as e:
        log.warning("%s✗ %s  (%s)", indent, name, e)
        print(f"{indent}  ✗ {name}  (error: {e})")
        return None

    releases = data.get("releases", {})
    if version:
        resolved_version = version
    elif spec:
        specifier = SpecifierSet(spec)
        if specifier.contains(latest_version):
            resolved_version = latest_version
        else:
            resolved_version = _resolve_version(releases, spec)
            if resolved_version is None:
                log.warning("%s✗ %s  (no version matching %s)", indent, name, spec)
                print(f"{indent}  ✗ {name}  (no version matching {spec})")
                return None
            log.debug("latest %s does not satisfy %s, resolved to %s", latest_version, spec, resolved_version)
    else:
        resolved_version = latest_version

    key = name.lower()
    if key in seen:
        log.debug("skip  %s==%s  (already resolved)", name, resolved_version)
        return None
    seen.add(key)

    files     = releases.get(resolved_version, [])
    best_file = _best_file(files)
    size      = best_file["size"]     if best_file else 0
    filename  = best_file["filename"] if best_file else "N/A"

    log.info("%s✓ %s==%s  →  %s  (%s)", indent, name, resolved_version, filename, _format_size(size))
    print(f"{indent}  ✓ {name}=={resolved_version}  →  {filename}")

    pkg = PackageInfo(name=name, version=resolved_version, size=size, filename=filename)

    if solo:
        return pkg

    env           = default_environment()
    active_extras = extras or set()
    requires_dist = data["info"].get("requires_dist") or []
    log.debug("%s%d dependencies declared", indent, len(requires_dist))

    for req_str in requires_dist:
        try:
            req = Requirement(req_str)
        except Exception as e:
            log.debug("could not parse requirement %r: %s", req_str, e)
            continue

        if req.marker:
            for extra in (active_extras or {None}):
                marker_env = {**env, "extra": extra} if extra else env
                if req.marker.evaluate(marker_env):
                    break
            else:
                log.debug("skip  %s  (marker not satisfied for extras=%s: %s)", req.name, active_extras, req.marker)
                continue

        dep = get_package_info(
            name      = req.name,
            version   = None,
            spec      = str(req.specifier),
            extras    = set(req.extras) if req.extras else None,
            seen      = seen,
            solo      = False,
            use_cache = use_cache,
            depth     = depth + 1,
        )
        if dep:
            pkg.dependencies.append(dep)

    return pkg


# ───────────────────────────── display ──────────────────────────────


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _total_size(pkg: PackageInfo) -> int:
    return pkg.size + sum(_total_size(d) for d in pkg.dependencies)


def _print_tree(pkg: PackageInfo, prefix: str = "", is_last: bool = True, is_root: bool = False) -> None:
    if is_root:
        total = _total_size(pkg)
        print(f"\n  {pkg.name}=={pkg.version}  ({_format_size(total)} total)")
        child_prefix = "  "
    else:
        connector    = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        print(f"{prefix}{connector}{pkg.name}=={pkg.version}  {_format_size(pkg.size)}")

    for i, dep in enumerate(pkg.dependencies):
        _print_tree(
            dep,
            prefix  = child_prefix,
            is_last = i == len(pkg.dependencies) - 1,
        )


# ───────────────────────────── main ─────────────────────────────────


def pip_size(package_spec: str, solo: bool = False, use_cache: bool = True) -> None:
    req     = Requirement(package_spec)
    name    = req.name
    spec    = str(req.specifier)

    log.info("starting  package=%s  spec=%s  solo=%s  cache=%s", name, spec or "latest", solo, use_cache)
    log.debug("cache dir: %s", _cache_dir())
    log.debug("platform tags (top 5): %s", list(SUPPORTED_TAGS.keys())[:5])

    cache_note = "  (cache disabled)" if not use_cache else ""
    print(f"\n🔍 Resolving '{package_spec}'...{cache_note}")

    pkg = get_package_info(name=name, spec=spec, extras=set(req.extras) if req.extras else None, solo=solo, use_cache=use_cache)

    if pkg is None:
        print("\n❌ Could not resolve package.")
        return

    log.info("resolved  total=%s", _format_size(_total_size(pkg)))
    _print_tree(pkg, is_root=True)
    print()


# ────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
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
        "--solo",
        action="store_true",
        help="Show size of the package itself only, without dependencies.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache and always fetch fresh data from PyPI.",
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
        help="Enable DEBUG logging (HTTP requests, wheel scoring, marker evaluation, cache).",
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
        pip_size(args.package, solo=args.solo, use_cache=not args.no_cache)
    except RuntimeError as e:
        log.error("%s", e)
        print(f"\n❌ Error: {e}")
        sys.exit(1)