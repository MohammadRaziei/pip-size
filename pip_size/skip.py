"""
Skip rules: dependencies that stay in the tree (with their size) but are not
counted towards the download total — e.g. because they are already installed.

Sources a rule set can be built from:
  * explicit names / glob patterns          ("numpy", "nvidia-*")
  * the current environment                 (importlib.metadata)
  * another interpreter's environment       (run that python and list its packages)
  * a `pip freeze` / requirements.txt file  ("-" reads stdin)
"""

from __future__ import annotations

import fnmatch
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from .errors import SkipSourceError

log = logging.getLogger(__name__)

_LISTER = (
    "import importlib.metadata as m\n"
    "for d in m.distributions():\n"
    "    n = d.metadata['Name']\n"
    "    if n: print(n + '==' + d.version)\n"
)


def parse_freeze(text: str) -> dict[str, str | None]:
    """
    Parse `pip freeze` / requirements.txt text into {canonical_name: version_or_None}.
    A line without an exact pin (``name``, ``name>=1``, ``name @ url``) maps to None,
    meaning "present, version unknown".
    """
    found: dict[str, str | None] = {}
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement:
            log.debug("freeze: ignoring unparseable line %r", raw)
            continue
        version = None
        for spec in req.specifier:
            if spec.operator in ("==", "===") and "*" not in spec.version:
                version = spec.version
        found[canonicalize_name(req.name)] = version
    return found


def installed_packages(python: str | None = None) -> dict[str, str | None]:
    """Packages installed in this interpreter, or in *python* if given."""
    if python is None or Path(python).resolve() == Path(sys.executable).resolve():
        import importlib.metadata as metadata

        return {
            canonicalize_name(d.metadata["Name"]): d.version
            for d in metadata.distributions()
            if d.metadata["Name"]
        }
    try:
        proc = subprocess.run(  # noqa: S603
            [python, "-c", _LISTER], capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SkipSourceError(f"cannot run interpreter {python!r}: {exc}") from exc
    if proc.returncode != 0:
        raise SkipSourceError(f"{python!r} failed to list packages: {proc.stderr.strip()[:200]}")
    return parse_freeze(proc.stdout)


def read_freeze_file(path: str) -> dict[str, str | None]:
    """Read a freeze/requirements file. ``-`` means stdin."""
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SkipSourceError(f"cannot read skip file {path!r}: {exc}") from exc
    return parse_freeze(text)


def _split_names(names: str | Iterable[str]) -> list[str]:
    items = [names] if isinstance(names, str) else list(names)
    out: list[str] = []
    for item in items:
        for token in re.split(r"[,\s]+", item):
            if token:
                canon = canonicalize_name(token)
                if canon not in out:
                    out.append(canon)
    return out


class SkipRules:
    """Decides whether a dependency is skipped, and why."""

    def __init__(
        self,
        names:     str | Iterable[str]             = (),
        installed: Mapping[str, str | None] | None = None,
    ) -> None:
        self._patterns  = _split_names(names)
        self._installed = {canonicalize_name(k): v for k, v in (installed or {}).items()}

    def __bool__(self) -> bool:
        return bool(self._patterns or self._installed)

    @classmethod
    def build(
        cls,
        names:          str | Iterable[str] = (),
        skip_installed: bool                = False,
        freeze_files:   Iterable[str]       = (),
        python:         str | None          = None,
    ) -> SkipRules:
        installed: dict[str, str | None] = {}
        if skip_installed or python:
            installed.update(installed_packages(python))
        for path in freeze_files:
            installed.update(read_freeze_file(path))
        return cls(names, installed)

    def match(self, name: str, specifier: str = "") -> str | None:
        """Return the reason this package is skipped, or None if it is not."""
        canon = canonicalize_name(name)

        for pattern in self._patterns:
            if fnmatch.fnmatchcase(canon, pattern):
                return "skip list"

        if canon in self._installed:
            version = self._installed[canon]
            if version is None:
                return "already installed"
            try:
                spec = SpecifierSet(specifier) if specifier else SpecifierSet()
            except InvalidSpecifier:
                spec = SpecifierSet()
            if spec.contains(version, prereleases=True):
                return f"installed {version}"
            log.debug("%s %s is installed but does not satisfy %s - not skipped", name, version, specifier)
        return None
