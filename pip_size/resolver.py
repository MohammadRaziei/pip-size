"""BFS dependency resolver: markers, extras, skip rules, conflict detection."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, cast

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .client import PyPIClient
from .errors import ResolutionError
from .models import Conflict, PackageInfo
from .selector import WheelSelector, pick_release
from .skip import SkipRules
from .wheel import LocalWheel

log = logging.getLogger(__name__)


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

    _ENV = cast("dict[str, str]", default_environment())

    def __init__(
        self,
        client:         PyPIClient,
        all_extras:     bool                    = False,
        all_extras_deep: bool                   = False,
        extras_filter:  set[str] | None         = None,
        no_deps:        bool                    = False,
        skip:           SkipRules | None        = None,
        progress:       Callable[[str], None] | None = None,
    ) -> None:
        self._client        = client
        self._all_extras    = all_extras           # every extra of the ROOT package
        self._all_extras_deep = all_extras_deep    # ... and of every dependency, at every level
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
                filename=wheel.filename, local=True, built_from=wheel.built_from,
            )
            requires: list[str] = wheel.requires_dist
            origin = "built from source" if wheel.built_from else "local file"
            self._say(f"{indent}  ✓ {pkg.name}=={pkg.version}  →  {pkg.filename}  ({origin})")
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

            triggered_by = self._evaluate_marker(req, active_extras, item.depth)
            if triggered_by is _SKIP:
                continue
            via_extra = triggered_by if isinstance(triggered_by, str) else None

            next_items.append(_WorkItem(
                name      = req.name,
                spec      = str(req.specifier),
                extras    = set(req.extras) if req.extras else None,
                via_extra = via_extra,
                parent    = pkg,
                depth     = item.depth + 1,
            ))

        return next_items

    def _evaluate_marker(self, req: Requirement, active_extras: set[str], depth: int = 0) -> object:
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
                 met; label with the extra name extracted from the marker. This applies to
                 the root package only (depth 0), as pip would: extras of a dependency are
                 activated only when its parent asks for them (`httpx[http2]`).
               - WITH    --all-extras-deep → same, but at every level of the tree. Unbounded:
                 test/dev extras of dependencies are included, so trees can reach thousands
                 of packages.
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
        if not (self._all_extras_deep or (self._all_extras and depth == 0)):
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
