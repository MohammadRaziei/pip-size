<div align="center">

<img src="https://raw.githubusercontent.com/mohammadraziei/pip-size/master/assets/logo.svg" width="128" alt="pip-size logo"/>

# pip-size

**See the real install size of a PyPI package, dependencies included, without downloading anything.**<br/>
No pip subprocess, no virtualenv, no wheel downloads.<br/>
Use it as a command-line tool, or import it as a Python library.

[![PyPI](https://img.shields.io/pypi/v/pip-size.svg)](https://pypi.org/project/pip-size)
[![Python](https://img.shields.io/pypi/pyversions/pip-size.svg)](https://pypi.org/project/pip-size)
[![CI](https://github.com/mohammadraziei/pip-size/actions/workflows/wheel.yml/badge.svg)](https://github.com/mohammadraziei/pip-size/actions/workflows/wheel.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.txt)

[Quick start](#quick-start) ·
[Python API](#python-api) ·
[Skipping](#skipping-dependencies) ·
[Local wheels](#local-wheels) ·
[How it works](#how-it-works)

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
metadata and adds it up, so you can compare `httpx` with `requests` or check what your own
release costs your users before anyone installs anything.

## Highlights

- **The whole tree, with sub-totals.** Every node shows its own size; nodes with dependencies also show the total of their sub-tree, so the heavy branch is easy to spot.
- **Nothing is downloaded.** Versions, file names and sizes come from the PyPI Simple API; only the dependency list of the chosen release is fetched separately. A cold run of `fastapi[standard]` took about a second in our test (one run, fast connection); results are cached for 24 hours.
- **Picks files the way pip does.** Wheel tags of the running interpreter, `requires-python`, environment markers and extras are honoured; yanked files are ignored.
- **Works on a wheel you just built.** Point it at a `.whl` file: its size and `METADATA` are read from disk, the rest is resolved from PyPI.
- **Skip what is already there.** Keep numpy, torch or anything installed in the tree with its size, but leave it out of the total.
- **Made for scripts and CI.** `--quiet --bytes` prints one number, `--json` prints only JSON, and an unresolvable package exits with status 1.
- **A Python library, not just a CLI.** `import pip_size` and call `pip_size.resolve()` (or `await pip_size.resolve_async()`): you get a typed result object and nothing is printed. See [Python API](#python-api).

## Installation

```bash
pip install pip-size
```

Requires Python 3.9 or newer. The same install gives you the `pip-size` command and the `pip_size` module.

| Command | Adds |
| --- | --- |
| `pip install "pip-size[socks]"` | SOCKS4/SOCKS5 proxy support (`aiohttp-socks`). |

## Quick start

```bash
# Full dependency tree
pip-size requests

# The package alone
pip-size requests --no-deps

# A specific version, or extras, like pip
pip-size "requests==2.31.0"
pip-size "requests[socks]"

# Just the number, for scripts
pip-size requests --quiet            # 655.3 KB
pip-size requests --quiet --bytes    # raw bytes

# A wheel you just built
pip-size ./dist/mypkg-1.0-py3-none-any.whl

# Do not count what the target machine already has
pip-size fastapi --skip-installed
```

CI size budget (fails the step when the package cannot be resolved or is too big):

```bash
test "$(pip-size ./dist/mypkg-*.whl --quiet --bytes --skip-installed)" -lt 5000000
```

## Python API

`pip-size` is also a library, and the command line is a thin layer on top of it. Everything the
CLI does is available from Python, and `resolve()` returns data instead of printing:

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

**Full reference, recipes and the result objects: [docs/python-api.md](https://github.com/MohammadRaziei/pip-size/blob/master/docs/python-api.md).**

## Skipping dependencies

Some dependencies cost your users nothing because they are already there (numpy on a data
science box, torch on a GPU image). Skipped packages **stay in the tree with their size**,
tagged `[skipped: ...]`, but are not added to the total and their own dependencies are not
expanded:

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

| Option | Skips |
| --- | --- |
| `--skip numpy,"nvidia-*"` | These names and glob patterns (repeatable; `Typing_Extensions` equals `typing-extensions`). |
| `--skip-installed` | Whatever is installed in the current environment. |
| `--installed-from /opt/venv/bin/python` | Whatever is installed in that interpreter's environment. Implies `--skip-installed`. |
| `--skip-file freeze.txt` | Every package in a `pip freeze` or requirements file. `-` reads stdin: `pip freeze \| pip-size pkg --skip-file -`, which also works for a freeze taken on another machine. |

Installed or frozen packages are skipped only when their version **satisfies the requirement**:
numpy 1.26 installed but `numpy>=2` required means pip would download it, so it still counts.
The root package is never skipped. The size shown for a skipped package is the file pip would
pick for the requirement, which is not necessarily the installed version.

## Local wheels

Give `pip-size` the path to a `.whl` instead of a package name:

```bash
pip-size ./dist/mypkg-1.0-py3-none-any.whl
pip-size "./dist/mypkg-1.0-py3-none-any.whl[server]"   # extra, like mypkg[server]
pip-size ./dist/mypkg-*.whl --skip-installed           # the glob must match exactly one file
```

The size is the file's size on disk, the dependency list comes from `*.dist-info/METADATA`
inside the wheel, and everything below it is resolved from PyPI as usual. The root line is
tagged `[local wheel]`. A missing file, a file that is not a zip, or a wheel without `METADATA`
gives a clear error and exit status 1.

## Extras

```bash
pip-size "requests[socks]"            # like pip: activate extras named in the spec
pip-size requests --extras socks      # same, via a flag
pip-size mypkg --extras "test,dev*"   # comma-separated, glob patterns allowed
pip-size mypkg --extras "*"           # every extra declared by the root package
pip-size requests -a                  # worst case: every extra of the root package
```

`-a` / `--all-extras` is the worst-case install of the package you ask about:

```text
$ pip-size requests -a

  requests==2.34.2  71.4 KB  (total: 2.2 MB)
  ├── certifi==2026.10.23  131.9 KB
  ├── chardet==7.7.0  1.6 MB  [extra: use-chardet-on-py3]
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  ├── PySocks==1.7.1  16.3 KB  [extra: socks]
  └── urllib3==2.8.0  132.5 KB
```

Extras of *dependencies* are activated only when their parent asks for them (`httpx[http2]`),
exactly as pip does. Each extra-gated package is labelled `[extra: <name>]`. `-a` can be combined
with `--extras`.

> [!WARNING]
> `--all-extras-deep` activates every extra of every package at every level, test and dev extras
> included. It is unbounded: when this README was written, `pip-size requests --all-extras-deep`
> resolved about 7,000 packages and reported 25.1 GB. It exists for completeness; for realistic
> numbers use `-a` or `--extras`.

## CLI essentials

| Flag | What it does |
| --- | --- |
| `--no-deps` | Size of the package itself only. |
| `--extras PATTERNS`, `-a` | Activate chosen extras / every extra of the root package. |
| `--all-extras-deep` | Every extra of every dependency too (unbounded, see the warning above). |
| `--skip`, `--skip-installed`, `--installed-from`, `--skip-file` | Keep dependencies in the tree but out of the total. |
| `--quiet`, `--bytes`, `--json` | Print only the total / raw bytes / the tree as JSON. |
| `--proxy URL`, `--no-cache`, `--clear-cache` | Network and cache control. |
| `--verbose`, `--extra-verbose` | INFO / DEBUG logging. |

<details>
<summary>Full option list</summary>

```text
pip-size [package] [options]

positional arguments:
  package               a requirement ("requests", "requests==2.31.0", "requests[security]")
                        or the path to a local .whl file (optionally "x.whl[extra]")

dependency options:
  --no-deps             Show size of the package itself only, no dependency resolution.
  -a, --all-extras      Activate EVERY extra declared by the root package (worst-case
                        install of this package). Extras of dependencies are activated
                        only when their parent requests them, like pip.
  --all-extras-deep     Like --all-extras, but also every extra of every dependency at
                        every level. Unbounded: can resolve thousands of packages.
  --extras PATTERNS     Activate specific extras for the ROOT package only.
                        Comma-separated, supports glob wildcards:
                          --extras test
                          --extras 'test,dev'
                          --extras 'dev*'
                          --extras '*'
                        Can be combined with -a.

skipping options:
  --skip NAMES          Package names or glob patterns to skip (comma-separated, repeatable).
  --skip-installed      Skip packages already installed in this environment
                        (only when the installed version satisfies the requirement).
  --installed-from PYTHON
                        Like --skip-installed, but for another interpreter's environment,
                        e.g. /opt/venv/bin/python. Implies --skip-installed.
  --skip-file FILE      Skip every package in a pip-freeze / requirements file (repeatable).
                        Use '-' for stdin:  pip freeze | pip-size pkg --skip-file -

output options:
  --quiet               Print only the total size (useful in shell scripts).
  --bytes               Report sizes in raw bytes instead of human-readable units.
  --json                Output the full dependency tree as JSON.

network options:
  --proxy URL           Proxy for all PyPI requests. Overrides HTTP_PROXY / ALL_PROXY.
                          HTTP:   --proxy http://user:pass@host:8080
                          SOCKS5: --proxy socks5://host:1080  (requires aiohttp-socks)
                          SOCKS4: --proxy socks4://host:1080  (requires aiohttp-socks)
  --no-cache            Bypass cache, always fetch fresh data from PyPI.
  --clear-cache         Delete all cached responses and exit.

logging:
  --verbose             Enable INFO logging.
  --extra-verbose       Enable DEBUG logging (HTTP, wheel scoring, markers, cache).
```

</details>

## JSON output

`pip-size idna --json` prints exactly this and nothing else on stdout:

```json
{
  "name": "idna",
  "version": "3.20",
  "size": "68.0 KB",
  "size_bytes": 69583,
  "total_size": "68.0 KB",
  "total_size_bytes": 69583,
  "filename": "idna-3.20-py3-none-any.whl"
}
```

Nodes with dependencies add a `dependencies` list. Skipped packages carry `"skipped": "<reason>"`,
extra-gated ones `"via_extra"`, a local wheel `"local": true`. The root node adds
`skipped_size`, `skipped_size_bytes` and `skipped_count` when something was skipped, and
`conflicts` / `warnings` when there are any.

## How it works

```mermaid
flowchart TB
    A["pip-size spec<br/>name, requirement or .whl path"] --> B{"local .whl?"}
    B -- yes --> C["read size and METADATA<br/>from the file"]
    B -- no --> D["PyPI Simple API<br/>versions, file names, sizes"]
    D --> E["pick version and best file<br/>for this Python and platform"]
    E --> F["PyPI release JSON<br/>requires_dist only"]
    C --> G["evaluate markers and extras"]
    F --> G
    G --> H{"skip rule matches?"}
    H -- yes --> I["keep in tree with size<br/>mark as skipped, do not expand"]
    H -- no --> J["next BFS layer<br/>fetched concurrently"]
    J --> D
    I --> K["tree, JSON or total"]
    J --> K
```

1. Lists every release of a project from the [Simple API](https://peps.python.org/pep-0691/) (JSON) with file names and sizes. A local wheel is read from disk instead.
2. Resolves the specifier to the highest matching version that has a file usable on your interpreter and platform. Pre-releases are considered only when the specifier asks for them or nothing else matches.
3. Chooses the best wheel by pip's tag priority, falling back to an sdist.
4. Reads `requires_dist` of that release and walks the graph in breadth-first layers, each layer fetched concurrently.
5. Evaluates environment markers and extras, applies skip rules, and caches every response for 24 hours.

**Limitations**

- Each package is resolved once, against the first requirement that mentions it. This is not a backtracking resolver like pip's. If another requirement in the tree rejects the selected version, `pip-size` reports a version conflict instead of ignoring it. A package shared by several parents appears once, under the first.
- Extras requested on a dependency that was already resolved without them are not merged in.
- Wheel and marker selection use the interpreter that runs `pip-size`, even with `--installed-from`.

> [!NOTE]
> Sizes are those of the distribution files on PyPI (compressed). Installed size on disk differs.

## Caching and proxies

Responses are cached for 24 hours: `~/.cache/pip-size/` on Linux and macOS (honours
`$XDG_CACHE_HOME`), `%LOCALAPPDATA%\pip-size\Cache\` on Windows.

```bash
pip-size --clear-cache                                  # wipe the cache
pip-size requests --no-cache                            # skip it for this run
pip-size requests --proxy http://user:pass@host:8080    # HTTP proxy
pip-size requests --proxy socks5://host:1080            # needs pip-size[socks]
HTTPS_PROXY=http://host:8080 pip-size requests          # environment variables also work
```

## License

MIT, see [LICENSE.txt](LICENSE.txt).
