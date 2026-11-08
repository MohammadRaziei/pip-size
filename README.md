<div align="center">

<img src="https://raw.githubusercontent.com/MohammadRaziei/pip-size/master/docs/assets/logo.svg" width="128" alt="pip-size logo"/>

# pip-size

**See the real install size of a PyPI package, dependencies included, without downloading anything.**<br/>
No pip subprocess, no virtualenv, no wheel downloads.<br/>
Use it as a command-line tool, or import it as a Python library.

[![PyPI](https://img.shields.io/pypi/v/pip-size.svg)](https://pypi.org/project/pip-size)
[![Python](https://img.shields.io/pypi/pyversions/pip-size.svg)](https://pypi.org/project/pip-size)
[![CI](https://github.com/MohammadRaziei/pip-size/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadRaziei/pip-size/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.txt)

[Documentation](https://mohammadraziei.github.io/pip-size/) ·
[Quick start](#quick-start) ·
[Python API](#python-api) ·
[Skipping](#skipping-dependencies) ·
[Local wheels](#local-wheels)

</div>

---

```bash
pip install pip-size
pip-size requests
```

```text
  requests==2.34.2  71.4 KB  (total: 655.3 KB)
  ├── certifi==2026.10.23  131.9 KB
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  └── urllib3==2.8.0  132.5 KB
```

A package's own size says little. What you pay in bandwidth, install time and disk is that
package **plus everything it pulls in**. `pip-size` resolves the full dependency tree from PyPI
metadata and adds it up, so you can compare `httpx` with `requests`, or check what your own
release costs your users before anyone installs it.

## Highlights

- **The whole tree, with sub-totals.** Every node shows its own size; nodes with dependencies also show the total of their sub-tree, so the heavy branch is easy to spot.
- **Nothing is downloaded.** Versions, file names and sizes come from the PyPI Simple API. Results are cached for 24 hours.
- **Picks files the way pip does.** Wheel tags of the running interpreter, `requires-python`, environment markers and extras are honoured; yanked files are ignored.
- **A Python library, not just a CLI.** `import pip_size` and call `pip_size.resolve()` (or `await pip_size.resolve_async()`): you get a typed result object and nothing is printed.
- **Works on a wheel you just built.** Point it at a `.whl`: its size and `METADATA` are read from disk.
- **Skip what is already there.** Keep numpy, torch or anything installed in the tree with its size, but leave it out of the total.
- **Made for scripts and CI.** `--quiet --bytes` prints one number, `--json` prints only JSON, and an unresolvable package exits with status 1.

## Installation

```bash
pip install pip-size
```

Requires Python 3.9 or newer. The same install gives you the `pip-size` command and the `pip_size`
module. For SOCKS proxies: `pip install "pip-size[socks]"`.

## Quick start

```bash
pip-size requests                      # full dependency tree
pip-size requests --no-deps            # the package alone
pip-size "requests==2.31.0"            # a specific version
pip-size "requests[socks]"             # with an extra, like pip
pip-size requests --quiet --bytes      # just the number: 671058
pip-size requests --json               # the tree as JSON
pip-size ./dist/mypkg-1.0-py3-none-any.whl     # a wheel you just built
pip-size fastapi --skip-installed      # do not count what is already installed
```

## Python API

`pip-size` is also a library, and the command line is a thin layer on top of it. `resolve()`
returns data instead of printing:

```python
import pip_size

res = pip_size.resolve("requests")

res.total_size                      # 671058, bytes to download
for pkg in res.packages:            # the whole tree, root first
    print(pkg.name, pkg.version, pkg.size)
```

```python
# Leave out what is already installed; skipped packages stay in the tree, marked
res = pip_size.resolve("fastapi", skip=["numpy"], skip_installed=True)
res.skipped_size, [p.name for p in res.skipped]

# A wheel you just built
res = pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")

# Several packages at once
res_a, res_b = await asyncio.gather(
    pip_size.resolve_async("requests"),
    pip_size.resolve_async("httpx"),
)
```

`resolve()` is for scripts and tests; in async code and Jupyter use `await resolve_async()`. Errors
are exceptions (`pip_size.ResolutionError` and friends, all deriving from `PipSizeError`), and
`res.to_dict()` gives plain data for `json.dumps`.
**[Python API reference](https://mohammadraziei.github.io/pip-size/python-api/)**

## Skipping dependencies

Some dependencies cost your users nothing because they are already there. Skipped packages stay
in the tree **with their size**, tagged `[skipped: ...]`, but are not added to the total:

```bash
pip-size requests --skip "idna,cert*"
```

```text
  requests==2.34.2  71.4 KB  (total: 455.5 KB)
  ├── certifi==2026.10.23  131.9 KB  [skipped: skip list]
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB  [skipped: skip list]
  └── urllib3==2.8.0  132.5 KB

  Total to download : 455.5 KB
  Skipped           : 199.9 KB  (2 package(s), not counted)
```

Skip by name or glob (`--skip`), what is installed here (`--skip-installed`), what is installed in
another environment (`--installed-from`), or what a `pip freeze` file lists (`--skip-file`).
[More on skipping](https://mohammadraziei.github.io/pip-size/guide/skipping/)

## Local wheels

```bash
pip-size ./dist/mypkg-1.0-py3-none-any.whl
pip-size "./dist/mypkg-1.0-py3-none-any.whl[server]"     # activate an extra
```

The size is the file's size on disk, the dependency list is read from the wheel's `METADATA`, and
everything below it is resolved from PyPI as usual: *how much will users download when they
install this?*, before it is on PyPI.
[More on local wheels](https://mohammadraziei.github.io/pip-size/guide/local-wheels/)

## Documentation

Full documentation: **<https://mohammadraziei.github.io/pip-size/>**

| | |
| --- | --- |
| [Command line](https://mohammadraziei.github.io/pip-size/cli/) | Every option, output format and exit code. |
| [Extras](https://mohammadraziei.github.io/pip-size/guide/extras/) | `pkg[extra]`, `--extras`, `-a`, and the worst case. |
| [Use in CI](https://mohammadraziei.github.io/pip-size/guide/ci/) | Fail a build when an install gets too big. |
| [Python API](https://mohammadraziei.github.io/pip-size/python-api/) | `resolve()`, result objects, errors, recipes. |
| [How it works](https://mohammadraziei.github.io/pip-size/concepts/how-it-works/) | Version and file selection, markers, extras, caching. |
| [Limitations](https://mohammadraziei.github.io/pip-size/concepts/limitations/) | What the numbers are, and are not. |

> [!NOTE]
> Sizes are those of the compressed distribution files on PyPI, for the Python and platform that
> run `pip-size`. Installed size on disk differs.

## License

MIT, see [LICENSE.txt](LICENSE.txt).
