# Errors and logging

## Exceptions

```python
import pip_size

try:
    res = pip_size.resolve(spec)
except pip_size.PipSizeError as exc:
    print(exc)
```

| Exception | Raised when |
| --- | --- |
| `PipSizeError` | Base class of everything below. It derives from `RuntimeError`. |
| `ResolutionError` | The root package cannot be resolved: unknown name, no matching version, an invalid requirement string, or a network failure on the root. |
| `WheelError` | A local wheel is missing, unreadable, or has no `METADATA`. |
| `SkipSourceError` | A skip source cannot be read: a missing freeze file, or an interpreter that cannot be run. |

A failure on a **dependency** is not an exception. The rest of the tree is still returned and the
problem is added to `Resolution.warnings`; see [Result objects](results.md#resolution).

## Calling resolve() inside an event loop

`resolve()` raises a plain `RuntimeError` if an event loop is already running, with a message that
tells you to use `await resolve_async(...)`. See [Overview](index.md#sync-or-async).

## Logging

`pip-size` logs through the standard `logging` module, under the `pip_size` logger: INFO for each
resolved package, DEBUG for HTTP requests, wheel scoring, marker evaluation and cache hits. It adds
no handlers, so nothing appears until you configure logging:

```python
import logging

logging.basicConfig(level=logging.INFO)
```

For per-package progress without logging, use the `progress` callback of
[`resolve()`](resolve.md#progress).
