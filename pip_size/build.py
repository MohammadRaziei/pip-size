"""
Build a wheel from a source tree, a VCS URL or an sdist archive, so that it can be sized like
any other local wheel.

    pip-size ./my-project                      directory with pyproject.toml / setup.py
    pip-size "git+https://github.com/o/r"      VCS URL (git, hg, svn, bzr)
    pip-size "mypkg @ git+https://..."         direct reference, extras allowed: "mypkg[x] @ ..."
    pip-size ./dist/mypkg-1.0.tar.gz           sdist archive

Building runs the project's build backend, exactly as ``pip wheel`` does. Directories are built
from a temporary copy, so the original tree is never modified.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from .errors import BuildError
from .wheel import LocalWheel, read_wheel

log = logging.getLogger(__name__)

BUILD_TIMEOUT = 900   # seconds

_VCS_RE      = re.compile(r"^(?:git|hg|svn|bzr)\+[A-Za-z][A-Za-z0-9+.-]*://", re.IGNORECASE)
_EXTRAS_RE   = re.compile(r"^(?P<target>.+?)(?:\[(?P<extras>[^\]]*)\])?$")
_ARCHIVE_EXT = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")
_COPY_IGNORE = shutil.ignore_patterns(
    ".venv", "venv", "node_modules", "__pycache__", "build", "dist", "*.egg-info",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site",
)


@dataclass
class SourceSpec:
    """Something a wheel can be built from."""
    target:  str                     # what the builder is given: a path or a URL
    kind:    str                     # "directory" | "archive" | "vcs" | "url"
    display: str                     # the user's spelling, for messages
    extras:  set[str] = field(default_factory=set)


def _looks_like_path(text: str) -> bool:
    return (
        os.sep in text
        or (os.altsep is not None and os.altsep in text)
        or text.startswith((".", "~"))
    )


def parse_source_spec(spec: str) -> SourceSpec | None:
    """
    Recognise *spec* as a buildable source, or return None if it is an ordinary requirement
    (``requests>=2``). A path-like spec that does not exist raises BuildError.
    """
    text = spec.strip()

    try:
        req = Requirement(text)
    except InvalidRequirement:
        pass
    else:
        # "name @ url": a direct reference. Plain requirements have no url.
        if req.url:
            kind = "vcs" if _VCS_RE.match(req.url) else "url"
            return SourceSpec(req.url, kind, text, set(req.extras))
        return None

    if _VCS_RE.match(text):
        return SourceSpec(text, "vcs", text)

    match = _EXTRAS_RE.match(text)
    target = match.group("target") if match else text
    extras = {e.strip() for e in ((match.group("extras") if match else None) or "").split(",") if e.strip()}

    if not _looks_like_path(target):
        return None
    path = Path(target).expanduser()
    if path.is_dir():
        if not ((path / "pyproject.toml").is_file() or (path / "setup.py").is_file()):
            raise BuildError(f"{target}: not an installable project (no pyproject.toml or setup.py)")
        return SourceSpec(str(path), "directory", text, extras)
    if path.is_file() and path.name.lower().endswith(_ARCHIVE_EXT):
        return SourceSpec(str(path), "archive", text, extras)
    raise BuildError(f"path not found, or not a directory or sdist archive: {target}")


# ── choosing and running a builder ──────────────────────────────────────────

def _has_pip() -> bool:
    return importlib.util.find_spec("pip") is not None


def builder_command(source: SourceSpec, out_dir: Path, target: str) -> list[str]:
    """The command that builds *target* into *out_dir*: pip if it is importable, else uv."""
    if _has_pip():
        return [
            sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-input",
            "--disable-pip-version-check", "-w", str(out_dir), target,
        ]
    uv = shutil.which("uv")
    if uv and source.kind in ("directory", "archive"):
        return [uv, "build", "--wheel", "--out-dir", str(out_dir), target]
    if source.kind in ("vcs", "url"):
        raise BuildError(
            f"cannot build {source.display}: building from a URL needs pip, which is not installed "
            f"in the Python that runs pip-size ({sys.executable}). Install pip there, or build the "
            "wheel yourself and pass the .whl file."
        )
    raise BuildError(
        "no wheel builder available: neither pip (in this Python) nor uv was found. "
        "Build the wheel yourself and pass the .whl file."
    )


async def _execute(cmd: list[str], cwd: str | None, timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise BuildError(f"the build did not finish within {int(timeout)} seconds") from None
    return proc.returncode or 0, out.decode(errors="replace")


@contextmanager
def _copy_of(source_dir: Path, workdir: Path) -> Iterator[Path]:
    """A throw-away copy of a source tree (keeps .git so VCS-based versions still work)."""
    dest = workdir / "src"
    shutil.copytree(source_dir, dest, ignore=_COPY_IGNORE, symlinks=True)
    yield dest


async def build_wheel(
    source:   SourceSpec,
    progress: Callable[[str], None] | None = None,
    timeout:  float                        = BUILD_TIMEOUT,
) -> LocalWheel:
    """Build a wheel from *source* in a temporary directory and read its metadata."""
    builder_command(source, Path("."), source.target)    # fail early if nothing can build it
    message = f"  ⚙ building a wheel from {source.display} ..."
    log.info("%s", message.strip())
    if progress is not None:
        progress(message)

    with tempfile.TemporaryDirectory(prefix="pip-size-build-") as tmp:
        workdir = Path(tmp)
        out_dir = workdir / "out"
        out_dir.mkdir()

        if source.kind == "directory":
            try:
                with _copy_of(Path(source.target), workdir) as copied:
                    cmd = builder_command(source, out_dir, str(copied))
                    code, output = await _execute(cmd, None, timeout)
            except OSError as exc:
                raise BuildError(f"cannot copy {source.display} for building: {exc}") from exc
        else:
            cmd = builder_command(source, out_dir, source.target)
            code, output = await _execute(cmd, None, timeout)

        if code != 0:
            tail = "\n".join(output.strip().splitlines()[-15:])
            raise BuildError(f"building {source.display} failed (exit status {code}):\n{tail}")

        wheels = sorted(out_dir.glob("*.whl"), key=lambda p: p.stat().st_mtime)
        if not wheels:
            raise BuildError(f"building {source.display} produced no wheel")
        built = read_wheel(wheels[-1])

    built.built_from = source.display
    return built
