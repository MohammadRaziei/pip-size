"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    python pip_size.py requests
    python pip_size.py requests --solo
    python pip_size.py "requests==2.31.0"
    python pip_size.py requests --verbose
    python pip_size.py requests --extra-verbose
"""

import json
import logging
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.version import Version


log = logging.getLogger("pip_size")

PYPI_JSON_API = "https://pypi.org/pypi/{package}/json"
PYPI_VER_API  = "https://pypi.org/pypi/{package}/{version}/json"

SUPPORTED_TAGS = {str(t): i for i, t in enumerate(sys_tags())}


# ───────────────────────────── http ─────────────────────────────────


def _fetch_json(url: str) -> dict:
    log.debug("GET %s", url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pip-size/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read().decode())
            log.debug("Response OK  (%s)", url)
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


def fetch_package(name: str, version: str | None = None) -> tuple[dict, str]:
    """Fetch PyPI JSON and return (data, resolved_version)."""
    if version:
        data = _fetch_json(PYPI_VER_API.format(package=name, version=version))
        return data, version
    data = _fetch_json(PYPI_JSON_API.format(package=name))
    return data, data["info"]["version"]


def get_package_info(
    name:    str,
    version: str | None = None,
    spec:    str        = "",
    seen:    set | None = None,
    solo:    bool       = False,
    depth:   int        = 0,
) -> PackageInfo | None:
    """
    Recursively resolve a package and its dependencies.
    Uses PyPI JSON API only — zero downloads.
    """
    if seen is None:
        seen = set()

    indent = "  " * depth
    log.info("%sresolving  %s%s", indent, name, f"  (spec: {spec})" if spec else "")

    try:
        data, resolved_version = fetch_package(name, version)
    except RuntimeError as e:
        log.warning("%s✗ %s  (%s)", indent, name, e)
        print(f"{indent}  ✗ {name}  (error: {e})")
        return None

    if spec:
        specifier = SpecifierSet(spec)
        if not specifier.contains(resolved_version):
            alt = _resolve_version(data.get("releases", {}), spec)
            if alt is None:
                log.warning("%s✗ %s  (no version matching %s)", indent, name, spec)
                print(f"{indent}  ✗ {name}  (no version matching {spec})")
                return None
            log.debug("version mismatch: latest=%s, using alt=%s", resolved_version, alt)
            resolved_version = alt
            data, _ = fetch_package(name, resolved_version)

    key = name.lower()
    if key in seen:
        log.debug("skip  %s==%s  (already resolved)", name, resolved_version)
        return None
    seen.add(key)

    best_file = _best_file(data.get("urls", []))
    size      = best_file["size"]     if best_file else 0
    filename  = best_file["filename"] if best_file else "N/A"

    log.info("%s✓ %s==%s  →  %s  (%s)", indent, name, resolved_version, filename, _format_size(size))
    print(f"{indent}  ✓ {name}=={resolved_version}  →  {filename}")

    pkg = PackageInfo(name=name, version=resolved_version, size=size, filename=filename)

    if solo:
        return pkg

    env           = default_environment()
    requires_dist = data["info"].get("requires_dist") or []

    log.debug("%s%d dependencies declared", indent, len(requires_dist))

    for req_str in requires_dist:
        try:
            req = Requirement(req_str)
        except Exception as e:
            log.debug("could not parse requirement %r: %s", req_str, e)
            continue

        if req.marker and not req.marker.evaluate(env):
            log.debug("skip  %s  (marker not satisfied: %s)", req.name, req.marker)
            continue

        if req.extras:
            log.debug("note: %s has extras %s (not resolved separately)", req.name, req.extras)

        dep = get_package_info(
            name    = req.name,
            version = None,
            spec    = str(req.specifier),
            seen    = seen,
            solo    = False,
            depth   = depth + 1,
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


def pip_size(package_spec: str, solo: bool = False) -> None:
    req     = Requirement(package_spec)
    name    = req.name
    spec    = str(req.specifier)
    version = spec[2:] if spec.startswith("==") else None

    log.info("starting  package=%s  spec=%s  solo=%s", name, spec or "latest", solo)
    log.debug("platform tags (top 5): %s", list(SUPPORTED_TAGS.keys())[:5])

    print(f"\n🔍 Resolving '{package_spec}'...")

    pkg = get_package_info(name=name, version=version, spec=spec, solo=solo)

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
    parser.add_argument("package", help='e.g. "requests" or "requests==2.31.0"')
    parser.add_argument(
        "--solo",
        action="store_true",
        help="Show size of the package itself only, without dependencies.",
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
        help="Enable DEBUG logging (includes HTTP requests, wheel scoring, marker evaluation).",
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

    try:
        pip_size(args.package, solo=args.solo)
    except RuntimeError as e:
        log.error("%s", e)
        print(f"\n❌ Error: {e}")
        sys.exit(1)