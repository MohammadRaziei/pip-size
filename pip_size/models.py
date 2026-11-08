"""Result data model: PackageInfo tree, Conflict, Resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


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
    built_from:   str | None = None   # the source it was built from, if it was built
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
        if self.built_from:
            out["built_from"] = self.built_from
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
