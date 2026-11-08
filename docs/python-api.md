# Python API

`pip-size` is a library as well as a command. The `pip-size` CLI is a thin layer on top of the
functions described here, so everything the command line can do is available from Python, and
the library returns data instead of printing it.

Requires Python 3.9 or newer. Everything below is importable from the top level: `import pip_size`.

- [Quick example](#quick-example)
- [resolve and resolve_async](#resolve-and-resolve_async)
- [Resolution](#resolution)
- [PackageInfo](#packageinfo)
- [Skipping dependencies](#skipping-dependencies)
- [Local wheels](#local-wheels)
- [Errors](#errors)
- [Progress and logging](#progress-and-logging)
- [Printing like the CLI](#printing-like-the-cli)
- [Recipes](#recipes)
- [Good to know](#good-to-know)

## Quick example

```python
import pip_size

res = pip_size.resolve("requests")

res.total_size                      # 671058, bytes to download
[p.name for p in res.packages]      # ['requests', 'certifi', 'charset_normalizer', 'idna', 'urllib3']

for pkg in res.packages:
    print(pkg.name, pkg.version, pkg.size)
```

## resolve and resolve_async

```text
resolve(spec, *, no_deps=False, all_extras=False, all_extras_deep=False, extras=None,
        skip=(), skip_installed=False, skip_files=(), installed_from=None, skip_rules=None,
        use_cache=True, proxy=None, progress=None) -> Resolution

async resolve_async(...)   same arguments, awaitable
```

The two functions take the same arguments. `resolve` is a synchronous wrapper for scripts, tests
and the REPL; `resolve_async` is for code that already runs an event loop. Neither prints anything.

**`spec`** is one of:

| Form | Example |
| --- | --- |
| A requirement string | `"requests"`, `"requests==2.31.0"`, `"requests[socks]"`, `"httpx>=0.27,<1"` |
| A `packaging.requirements.Requirement` | `Requirement("requests[socks]")` |
| A path to a local wheel (`str` or `os.PathLike`), optionally with extras | `"./dist/mypkg-1.0-py3-none-any.whl"`, `"./dist/mypkg-1.0-py3-none-any.whl[server]"` |

**Options** (all keyword-only):

| Option | Default | Meaning |
| --- | --- | --- |
| `no_deps` | `False` | Only the package itself, no dependency resolution. |
| `extras` | `None` | Extras to activate on the **root** package: a string (`"test,dev*"`) or an iterable of names or glob patterns. Same as `pkg[extra]`. |
| `all_extras` | `False` | Activate every extra declared by the root package (worst-case install of that package). |
| `all_extras_deep` | `False` | Also activate every extra of every dependency, at every level. Unbounded: it includes test and dev extras and can resolve thousands of packages. |
| `skip` | `()` | Names or glob patterns to keep in the tree but not count. A string (`"numpy,nvidia-*"`) or an iterable. |
| `skip_installed` | `False` | Also skip packages installed in this environment, when the installed version satisfies the requirement. |
| `skip_files` | `()` | Paths of `pip freeze` / requirements files whose packages are skipped. `"-"` reads stdin. |
| `installed_from` | `None` | Path of another Python executable whose environment `skip_installed` inspects. Implies `skip_installed`. |
| `skip_rules` | `None` | A prebuilt [`SkipRules`](#skiprules). When given, the four skip options above are ignored. |
| `use_cache` | `True` | Use the on-disk response cache (24 hours). |
| `proxy` | `None` | Proxy URL (`http://...`, `socks5://...`). Falls back to the usual proxy environment variables. |
| `progress` | `None` | Callback that receives one line of text per resolved package. |

**Returns** a [`Resolution`](#resolution).
**Raises** a subclass of [`PipSizeError`](#errors) when the root package cannot be resolved.

### Sync or async?

```python
# Script, notebook-free code, pytest: just call resolve()
res = pip_size.resolve("requests")

# Async code, Jupyter, anything with a running event loop: await resolve_async()
res = await pip_size.resolve_async("requests")
```

Calling `resolve()` while an event loop is running raises `RuntimeError` with a message that
points you to `resolve_async()`. This is the case in Jupyter, where top-level `await` works.

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
# httpx        561.1 KB  (7 packages)
# requests     655.3 KB  (5 packages)
# aiohttp        3.1 MB  (10 packages)
```

(The numbers depend on the day's PyPI releases and on your platform.)

## Resolution

What `resolve()` returns.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `root` | `PackageInfo` | The package you asked about, with its dependencies nested below it. |
| `total_size` | `int` | Bytes to download: the whole tree, **skipped packages not counted**. |
| `skipped_size` | `int` | Bytes of the skipped packages. |
| `packages` | `list[PackageInfo]` | The whole tree flattened, root first. Skipped packages included. |
| `skipped` | `list[PackageInfo]` | Only the skipped packages. |
| `conflicts` | `list[Conflict]` | Packages resolved to a version that another requirement in the tree rejects. |
| `warnings` | `list[str]` | Non-fatal problems, such as a dependency that could not be found. |
| `to_dict()` | `dict` | Plain data for `json.dumps`. All sizes are integers in bytes. |

```python
pip_size.resolve("idna").to_dict()
# {'name': 'idna', 'version': '3.20', 'size': 69583, 'total_size': 69583,
#  'filename': 'idna-3.20-py3-none-any.whl', 'skipped_size': 0, 'conflicts': [], 'warnings': []}
```

Nodes with dependencies add a `dependencies` list. `via_extra`, `skipped` and `local` appear on
the nodes they apply to. This differs from `pip-size --json`, which formats sizes as text and adds
`*_bytes` fields; `to_dict()` always uses integers.

### Conflict

Each package is resolved once, against the first requirement that mentions it. If another
requirement in the tree rejects that version, you get a `Conflict` instead of silence.

| Field | Meaning |
| --- | --- |
| `name`, `version` | The package and the version that was selected. |
| `requirement` | The specifier that rejects it, for example `"<2"`. |
| `required_by` | The package that asked for it, or `None` for the request itself. |

## PackageInfo

One node of the dependency tree.

| Field | Type | Meaning |
| --- | --- | --- |
| `name`, `version` | `str` | Package name and the selected version. |
| `size` | `int` | Bytes of the distribution file pip would download (a wheel if possible, otherwise an sdist). `0` with `filename == "N/A"` if the version has no file usable on this platform. |
| `filename` | `str` | That file's name. |
| `dependencies` | `list[PackageInfo]` | Direct dependencies, sorted by name. A package that several parents depend on appears once, under the first. |
| `via_extra` | `str \| None` | The extra that pulled this package in, if any. |
| `skipped` | `str \| None` | Why it is skipped (for example `"skip list"` or `"installed 1.26.4"`), or `None`. |
| `local` | `bool` | `True` for a wheel read from disk. |

| Method | Returns |
| --- | --- |
| `total_size(include_skipped=False)` | This node plus everything below it, in bytes. |
| `skipped_size()` | Bytes of skipped packages in this sub-tree. |
| `walk()` | Iterator over this node and all descendants, depth first. |
| `to_dict()` | Plain data for this sub-tree. |

## Skipping dependencies

A skipped package stays in the tree with its size and a reason, but it is not counted in
`total_size` and its own dependencies are not expanded. The root package is never skipped.

```python
res = pip_size.resolve("requests", skip=["idna"])

res.total_size                       # 601475, without idna
res.skipped_size                     # 69583
[(p.name, p.skipped) for p in res.skipped]       # [('idna', 'skip list')]
res.root.total_size(include_skipped=True)        # 671058, with it
```

| Source | How |
| --- | --- |
| Names and glob patterns | `skip="numpy,nvidia-*"` or `skip=["numpy", "nvidia-*"]`. Names are normalised, so `Typing_Extensions` equals `typing-extensions`. |
| This environment | `skip_installed=True` |
| Another environment | `installed_from="/opt/venv/bin/python"` |
| A freeze or requirements file | `skip_files=["frozen.txt"]`, or `"-"` for stdin |

Installed or frozen packages are skipped only if their version satisfies the requirement:
numpy 1.26 installed but `numpy>=2` required means pip would download it, so it still counts.
The size shown for a skipped package is that of the file pip would pick for the requirement, not
necessarily the installed version.

### SkipRules

Build the rules once and reuse them, for example to avoid listing installed packages for every call:

```python
from pip_size import SkipRules

rules = SkipRules.build(names="nvidia-*", skip_installed=True)

for name in ["requests", "httpx"]:
    res = pip_size.resolve(name, skip_rules=rules)

rules.match("nvidia-cublas-cu12")    # 'skip list'
rules.match("zzz-not-installed")     # None
```

`SkipRules(names=(), installed=None)` takes names and a mapping of `{package: version_or_None}`.
`SkipRules.build(names=(), skip_installed=False, freeze_files=(), python=None)` gathers the
installed packages for you. `match(name, specifier="")` returns the reason or `None`.

## Local wheels

Pass the path of a `.whl`. The size is the file's size on disk, the dependency list is read from
the `METADATA` inside the wheel, and the dependencies below it are resolved from PyPI.

```python
res = pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")
res.root.local          # True
res.root.size           # size of the file itself
```

To look at a wheel without resolving anything, use `read_wheel`:

```python
w = pip_size.read_wheel("./dist/pip_size-0.3.0-py3-none-any.whl")
w.name, w.version                # ('pip-size', '0.3.0')
w.size                           # bytes of the .whl file
w.requires_dist                  # ['aiohttp>=3.8.0', 'packaging', "aiohttp-socks; extra == 'socks'"]
w.requires_python                # '>=3.9'
w.provides_extra                 # ['socks']
```

`read_wheel` returns a `LocalWheel` (`path`, `name`, `version`, `size`, `requires_dist`,
`requires_python`, `provides_extra`, and a `filename` property). A missing file, a file that is not
a zip, or a wheel without `METADATA` raises `WheelError`.

## Errors

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
| `ResolutionError` | The root package cannot be resolved: unknown name, no matching version, invalid requirement string, or a network failure on the root. |
| `WheelError` | A local wheel is missing, unreadable or has no `METADATA`. |
| `SkipSourceError` | A skip source cannot be read: a missing freeze file, or an interpreter that cannot be run. |

A failure on a **dependency** is not an exception: the rest of the tree is still returned and the
problem is added to `res.warnings`.

## Progress and logging

`progress` receives one line per resolved package, the same lines the CLI prints while it works:

```python
lines = []
pip_size.resolve("idna", progress=lines.append)
lines    # ['  ✓ idna==3.20  →  idna-3.20-py3-none-any.whl']
```

The library also logs through the standard `logging` module under the `pip_size` logger
(INFO for resolved packages, DEBUG for HTTP requests, wheel scoring, markers and cache hits):

```python
import logging
logging.basicConfig(level=logging.INFO)
```

## Printing like the CLI

`Printer` formats a `Resolution` the way the command line does, and `Printer.format_size` turns
bytes into text:

```python
from pip_size import Printer

res = pip_size.resolve("idna")
Printer().print_tree(res)                    # the tree
Printer().print_json(res)                    # the CLI's --json document
Printer(as_bytes=True).print_quiet(res)      # 69583
Printer.format_size(69583)                   # '68.0 KB'
```

`PipSize` is the CLI behaviour as a class (resolve, then print). `PipSize(no_deps=True).run("requests")`
returns `True` if the package was resolved; it is what `pip-size` calls. For programs, prefer
`resolve()`.

## Recipes

### Fail a build when the wheel is too big

```python
import glob
import pip_size

wheel = sorted(glob.glob("dist/mypkg-*.whl"))[-1]
res = pip_size.resolve(wheel, skip_installed=True)

BUDGET = 5_000_000
assert res.total_size < BUDGET, f"install size {res.total_size} bytes exceeds {BUDGET}"
```

(`skip_installed=True` leaves out whatever your CI environment already has. Drop it to count
everything.)

### Find the heaviest contributors

```python
from pip_size import Printer

res = pip_size.resolve("httpx[http2]")
for pkg in sorted(res.packages, key=lambda p: p.size, reverse=True)[:3]:
    print(f"{pkg.name:<20} {Printer.format_size(pkg.size)}")
```

### Follow the heaviest branch

```python
def heaviest_branch(pkg):
    deps = pkg.dependencies
    return [pkg.name] + (heaviest_branch(max(deps, key=lambda d: d.total_size())) if deps else [])

" -> ".join(heaviest_branch(pip_size.resolve("httpx[http2]").root))
# 'httpx -> anyio -> typing_extensions'
```

### Export to JSON

```python
import json

with open("size-report.json", "w") as f:
    json.dump(pip_size.resolve("requests").to_dict(), f, indent=2)
```

### What does a deployment image already have?

Take a freeze from the image once, then size packages against it:

```bash
docker run --rm my-base-image pip freeze > base-freeze.txt
```

```python
res = pip_size.resolve("fastapi", skip_files=["base-freeze.txt"])
res.total_size      # only what the image is missing
```

## Good to know

- **Sizes are those of the compressed distribution files on PyPI**, not the size installed on disk.
- **Results depend on the machine that runs `pip-size`.** The wheel for your Python version and platform is chosen, and environment markers are evaluated for your interpreter, so `charset_normalizer`, for example, differs between Python versions. This is also true with `installed_from`: only the *skip list* comes from the other interpreter.
- **The resolver is not pip's.** Each package is resolved once, against the first requirement seen, with no backtracking. Conflicts are reported in `res.conflicts` rather than solved.
- **Responses are cached for 24 hours** (`pip_size.Cache.clear()` deletes the cache and returns the number of files removed). Pass `use_cache=False` to bypass it.
- **Network access** is needed unless the cache is warm; there is a 30-second timeout per request.
