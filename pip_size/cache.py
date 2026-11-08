"""On-disk response cache (24h TTL, atomic writes)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────

class Cache:
    TTL = 24 * 60 * 60   # seconds

    @staticmethod
    def directory() -> Path:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            return base / "pip-size" / "Cache"
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        return base / "pip-size"

    @classmethod
    def _path(cls, url: str) -> Path:
        parsed = urlparse(url)
        raw    = f"{parsed.netloc}{parsed.path}".strip("/")
        safe   = re.sub(r"[^A-Za-z0-9._+-]+", "_", raw)
        return cls.directory() / f"{safe}.json"

    @classmethod
    def read(cls, url: str) -> dict | None:
        path = cls._path(url)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > cls.TTL:
            log.debug("cache expired (%.0fh old) %s", age / 3600, path.name)
            path.unlink(missing_ok=True)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            log.debug("cache hit  %s", path.name)
            return data
        except (json.JSONDecodeError, OSError):
            path.unlink(missing_ok=True)
            return None

    @classmethod
    def write(cls, url: str, data: dict) -> None:
        path = cls._path(url)
        tmp  = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)           # atomic: readers never see a half-written file
            log.debug("cache write  %s", path.name)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            log.debug("cache write failed: %s", e)

    @classmethod
    def clear(cls) -> int:
        """Delete all cached files. Returns the number of files removed."""
        directory = cls.directory()
        if not directory.exists():
            return 0
        count = len(list(directory.glob("*.json")))
        shutil.rmtree(directory, ignore_errors=True)
        return count
