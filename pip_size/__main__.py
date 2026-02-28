"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    pip-size "requests"
    pip-size "requests" --no-deps
    pip-size "requests==2.31.0"
    pip-size "requests" --verbose
    pip-size "requests" --extra-verbose
    pip-size "requests" --no-cache
    pip-size --clear-cache
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
CACHE_TTL_SECONDS = 24 * 60 * 60

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
    # strip the common prefix and replace slashes to get a safe filename
    # e.g. https://pypi.org/pypi/requests/json  ->  requests.json
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
    # last three parts are always python-abi-platform
    return parts[-3], parts[-2], parts[-1]


def _wheel_priority(filename: str) -> int:
    """
    Return the priority of a wheel for this platform (lower = better).
    Each tag component can be dot-separated (e.g. cp311.cp310-abi3-linux_x86_64),
    so we check all combinations against SUPPORTED_TAGS.
    Returns sys.maxsize if no compatible tag is found.
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
    Pick the best compatible distribution file for this platform.
    Wheels are preferred over sdist; among wheels the one with the lowest
    priority index (i.e. closest match in sys_tags) wins.
    """
    env = default_environment()
    compatible = []
    for f in files:
        # skip files that require a python version we don't have
        requires_python = f.get("requires_python")
        if requires_python:
            if not SpecifierSet(requires_python).contains(env["python_version"]):
                log.debug("skip  %s  (requires_python %s)", f["filename"], requires_python)
                continue

        if f["filename"].endswith(".whl"):
            priority = _wheel_priority(f["filename"])
            if priority < sys.maxsize:
                compatible.append((0, priority, f))  # 0 = wheel (preferred over sdist)
        else:
            compatible.append((1, 0, f))             # 1 = sdist (last resort)

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
    size:         int                # bytes of the chosen distribution file
    filename:     str                # e.g. requests-2.31.0-py3-none-any.whl
    via_extra:    str | None = None  # set when this dep was pulled in by an extra marker
    dependencies: list["PackageInfo"] = field(default_factory=list)


def _resolve_version(releases: dict, specifier_str: str) -> str | None:
    """Pick the latest stable version that satisfies specifier_str."""
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
    """
    Fetch the PyPI JSON for a package (always the /pypi/{name}/json endpoint).
    Returns (full_data, latest_version). Exactly one HTTP request per package.
    Version resolution for older releases is done from data["releases"] — no second request.
    """
    data = _fetch_json(PYPI_JSON_API.format(package=name), use_cache)
    return data, data["info"]["version"]


def get_package_info(
    name:      str,
    version:   str | None    = None,
    spec:      str           = "",
    extras:    set[str] | None = None,
    seen:      set | None    = None,
    solo:      bool          = False,
    use_cache: bool          = True,
    quiet:     bool          = False,
    depth:     int           = 0,
) -> PackageInfo | None:
    """
    Recursively fetch and resolve a package and all its dependencies.

    - version : if provided, pins to an exact version (skips specifier logic)
    - spec    : PEP 440 specifier string, e.g. ">=2.0,<3"
    - extras  : active extras from the parent requirement, e.g. {"security"}
    - seen    : set of already-resolved package names (guards against cycles)
    - solo    : if True, skip dependency resolution
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

    # pick the right version — all info lives in data["releases"], no second fetch needed
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
                if not quiet:
                    print(f"{indent}  ✗ {name}  (no version matching {spec})")
                return None
            log.debug("latest %s does not satisfy %s, resolved to %s", latest_version, spec, resolved_version)
    else:
        resolved_version = latest_version

    # deduplicate: skip if we already resolved this package in this run
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
    if not quiet:
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

        # evaluate the marker against each active extra; include the dep if any matches
        triggered_by: str | None = None
        if req.marker:
            for extra in (active_extras or {None}):
                marker_env = {**env, "extra": extra} if extra else env
                if req.marker.evaluate(marker_env):
                    triggered_by = extra  # remember which extra unlocked this dep
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
            quiet     = quiet,
            depth     = depth + 1,
        )
        if dep:
            dep.via_extra = triggered_by
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



def _to_dict(pkg: PackageInfo, as_bytes: bool = False) -> dict:
    """Serialize a PackageInfo tree to a plain dict suitable for JSON output."""
    size = pkg.size if as_bytes else _format_size(pkg.size)
    total = _total_size(pkg)
    result = {
        "name":         pkg.name,
        "version":      pkg.version,
        "size":         pkg.size if as_bytes else _format_size(pkg.size),
        "total_size":   total if as_bytes else _format_size(total),
        "filename":     pkg.filename,
    }
    if pkg.via_extra:
        result["via_extra"] = pkg.via_extra
    if pkg.dependencies:
        result["dependencies"] = [_to_dict(d, as_bytes) for d in pkg.dependencies]
    return result

def _print_tree(pkg: PackageInfo, prefix: str = "", is_last: bool = True, is_root: bool = False, fmt=_format_size) -> None:
    if is_root:
        total = _total_size(pkg)
        print(f"\n  {pkg.name}=={pkg.version}  ({fmt(total)} total)")
        child_prefix = "  "
    else:
        connector    = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        extra_tag = f"  [extra: {pkg.via_extra}]" if pkg.via_extra else ""
        print(f"{prefix}{connector}{pkg.name}=={pkg.version}  {fmt(pkg.size)}{extra_tag}")

    for i, dep in enumerate(pkg.dependencies):
        _print_tree(
            dep,
            prefix  = child_prefix,
            is_last = i == len(pkg.dependencies) - 1,
            fmt     = fmt,
        )


# ───────────────────────────── main ─────────────────────────────────


def pip_size(package_spec: str, no_deps: bool = False, use_cache: bool = True, quiet: bool = False, as_bytes: bool = False, as_json: bool = False) -> None:
    req  = Requirement(package_spec)
    name = req.name
    spec = str(req.specifier)

    log.info("starting  package=%s  spec=%s  no_deps=%s  cache=%s", name, spec or "latest", no_deps, use_cache)
    log.debug("cache dir: %s", _cache_dir())
    log.debug("platform tags (top 5): %s", list(SUPPORTED_TAGS.keys())[:5])

    if not quiet:
        cache_note = "  (cache disabled)" if not use_cache else ""
        print(f"\n🔍 Resolving '{package_spec}'...{cache_note}")

    pkg = get_package_info(
        name      = name,
        spec      = spec,
        extras    = set(req.extras) if req.extras else None,
        solo      = no_deps,
        use_cache = use_cache,
        quiet     = quiet,
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


# ────────────────────────────────────────────────────────────────────

def main():
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
        pip_size(args.package, no_deps=args.no_deps, use_cache=not args.no_cache, quiet=args.quiet, as_bytes=args.bytes, as_json=args.json)
    except RuntimeError as e:
        log.error("%s", e)
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()