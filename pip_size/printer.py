"""Human and machine output for a Resolution."""

from __future__ import annotations

import json

from .models import PackageInfo, Resolution


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
        size: float = size_bytes
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
