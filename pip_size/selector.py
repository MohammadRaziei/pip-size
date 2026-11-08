"""Choosing what pip would install: best file for this platform, best version for a specifier."""

from __future__ import annotations

import logging
import sys

from packaging.markers import default_environment
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.version import InvalidVersion, Version

log = logging.getLogger(__name__)


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
