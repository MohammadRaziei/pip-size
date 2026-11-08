"""Read size and metadata straight out of a local ``.whl`` file."""

from __future__ import annotations

import email.parser
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from .errors import WheelError

_METADATA_RE = re.compile(r"^[^/]+\.dist-info/METADATA$")
_MAX_METADATA_BYTES = 10 * 1024 * 1024
_WHEEL_SPEC_RE = re.compile(r"^(?P<path>.+?\.whl)(?:\[(?P<extras>[^\]]*)\])?$", re.IGNORECASE)


@dataclass
class LocalWheel:
    path:            Path
    name:            str
    version:         str
    size:            int                       # bytes of the .whl file itself
    requires_dist:   list[str]                 = field(default_factory=list)
    requires_python: str | None                = None
    provides_extra:  list[str]                 = field(default_factory=list)
    built_from:      str | None                = None   # set when the wheel was built from source

    @property
    def filename(self) -> str:
        return self.path.name


def parse_wheel_spec(spec: str) -> tuple[Path, set[str]] | None:
    """
    Recognise ``path/to/pkg-1.0-py3-none-any.whl`` and ``...whl[extra1,extra2]``.
    Returns (path, extras), or None if *spec* is not a wheel path.
    """
    match = _WHEEL_SPEC_RE.match(spec.strip())
    if not match:
        return None
    extras = {e.strip() for e in (match.group("extras") or "").split(",") if e.strip()}
    return Path(match.group("path")).expanduser(), extras


def read_wheel(path: str | Path) -> LocalWheel:
    """Open *path* as a zip, parse ``*.dist-info/METADATA`` and return its dependency data."""
    path = Path(path)
    if not path.is_file():
        raise WheelError(f"wheel not found: {path}")

    try:
        with zipfile.ZipFile(path) as zf:
            candidates = [i for i in zf.infolist() if _METADATA_RE.match(i.filename)]
            if not candidates:
                raise WheelError(f"{path.name}: no *.dist-info/METADATA inside the wheel")
            info = candidates[0]
            if info.file_size > _MAX_METADATA_BYTES:
                raise WheelError(f"{path.name}: METADATA is implausibly large ({info.file_size} bytes)")
            raw = zf.read(info)
    except zipfile.BadZipFile as exc:
        raise WheelError(f"{path.name}: not a valid wheel (zip) file: {exc}") from exc
    except OSError as exc:
        raise WheelError(f"{path.name}: cannot read file: {exc}") from exc

    msg = email.parser.BytesParser().parsebytes(raw, headersonly=True)

    def clean(value: object) -> str:
        return " ".join(str(value).split())

    name = msg.get("Name")
    version = msg.get("Version")
    if not name or not version:
        try:
            f_name, f_version, _, _ = parse_wheel_filename(path.name)
        except InvalidWheelFilename as exc:
            raise WheelError(f"{path.name}: METADATA has no Name/Version and the filename is not a valid wheel name") from exc
        name = name or str(f_name)
        version = version or str(f_version)

    return LocalWheel(
        path            = path,
        name            = clean(name),
        version         = clean(version),
        size            = path.stat().st_size,
        requires_dist   = [clean(v) for v in (msg.get_all("Requires-Dist") or [])],
        requires_python = clean(msg["Requires-Python"]) if msg.get("Requires-Python") else None,
        provides_extra  = [clean(v) for v in (msg.get_all("Provides-Extra") or [])],
    )
