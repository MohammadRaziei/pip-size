# Python API

`pip-size` is a library as well as a command. The `pip-size` CLI is a thin layer on top of the
functions documented here, so everything the command line can do is available from Python, and the
library **returns data instead of printing it**.

```python
import pip_size

res = pip_size.resolve("requests")

res.total_size                      # 671058, bytes to download
[p.name for p in res.packages]      # ['requests', 'certifi', 'charset_normalizer', 'idna', 'urllib3']

for pkg in res.packages:
    print(pkg.name, pkg.version, pkg.size)
```

Requires Python 3.9 or newer. The package ships type information (`py.typed`).

## Sync or async

| Call | Use it in |
| --- | --- |
| `pip_size.resolve(...)` | Scripts, tests, the REPL: any code without a running event loop. |
| `await pip_size.resolve_async(...)` | Async code, Jupyter, anything with a running event loop. |

Calling `resolve()` while an event loop is running raises a `RuntimeError` that points you to
`resolve_async()`. In Jupyter, where top-level `await` works, use `await resolve_async(...)`.

### Resolving several packages at once

Each call opens its own HTTP session, so calls are independent and can run concurrently. The
on-disk cache is shared.

```python
import asyncio
import pip_size
from pip_size import Printer

async def compare(names):
    results = await asyncio.gather(*(pip_size.resolve_async(n) for n in names))
    for name, res in sorted(zip(names, results), key=lambda item: item[1].total_size):
        print(f"{name:<10} {Printer.format_size(res.total_size):>10}  ({len(res.packages)} packages)")

asyncio.run(compare(["requests", "httpx", "aiohttp"]))
```

## What is in the package

| Name | Page |
| --- | --- |
| `resolve`, `resolve_async` | [resolve()](resolve.md) |
| `Resolution`, `PackageInfo`, `Conflict`, `Printer`, `PipSize` | [Result objects](results.md) |
| `SkipRules` | [Skip rules](skip-rules.md) |
| `read_wheel`, `LocalWheel` | [Wheels](wheels.md) |
| `PipSizeError`, `ResolutionError`, `WheelError`, `SkipSourceError` | [Errors and logging](errors.md) |
| `Cache`, `DependencyResolver` | Lower-level building blocks, see [How it works](../concepts/how-it-works.md). |

!!! note "Stability"
    The version is below 1.0: the public API above can still change between minor releases.
    Changes are listed in the [changelog](../changelog.md).
