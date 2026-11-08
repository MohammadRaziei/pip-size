"""Exception hierarchy. Everything pip-size raises on purpose derives from PipSizeError."""

from __future__ import annotations


class PipSizeError(RuntimeError):
    """Base class for all expected pip-size failures."""


class ResolutionError(PipSizeError):
    """The requested package (or local wheel) could not be resolved."""


class WheelError(PipSizeError):
    """A local .whl file is missing, unreadable, or has no usable metadata."""


class SkipSourceError(PipSizeError):
    """A skip source (freeze file, interpreter) could not be read."""
