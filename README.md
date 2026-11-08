# pip-size

[![PyPI - Version](https://img.shields.io/pypi/v/pip-size.svg)](https://pypi.org/project/pip-size)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pip-size.svg)](https://pypi.org/project/pip-size)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Calculate the real download size of PyPI packages and their dependencies — zero downloads, no pip subprocess, pure PyPI JSON API.**

---

## Why pip-size?

A package's size alone tells you very little. What you actually pay — in bandwidth, install time, and disk space — is the size of that package **plus every dependency it pulls in**.

A library advertised as "lightweight" may itself be tiny, while silently dragging in hundreds of megabytes of transitive dependencies. That's not a fair comparison. `pip-size` makes the full picture visible before you install anything.

Use it to:

- **Compare alternatives fairly** — e.g. `httpx` vs `requests` vs `aiohttp`, each with their full dependency tree, without installing any of them
- **Audit your own packages** — check what you're actually shipping to your users
- **Spot unexpectedly heavy dependencies** — find which dep in the tree is responsible for the bulk of the size
- **Understand optional extras** — see exactly how much `requests[security]` adds over plain `requests`
- **Automate size checks** in CI with `--quiet` and `--bytes`

---

## Installation

```bash
pip install pip-size
```

---

## Quick Start

```bash
# Full dependency tree
pip-size requests

# Package alone, no deps
pip-size requests --no-deps

# Specific version
pip-size "requests==2.31.0"

# With extras
pip-size "requests[security]"

# Quiet mode — just the number, useful in scripts
pip-size requests --quiet

# JSON output
pip-size requests --json

# A wheel you just built (size + METADATA are read from the file)
pip-size ./dist/mypkg-1.0-py3-none-any.whl

# Don't count what the target machine already has
pip-size fastapi --skip-installed
pip-size fastapi --skip numpy,pandas
```

### Example Output

```
$ pip-size requests

🔍 Resolving 'requests'...
  ✓ requests==2.32.5  →  requests-2.32.5-py3-none-any.whl
    ✓ urllib3==2.3.0  →  urllib3-2.3.0-py3-none-any.whl
    ✓ charset-normalizer==3.4.1  →  charset_normalizer-3.4.1-py3-none-any.whl
    ✓ certifi==2025.1.31  →  certifi-2025.1.31-py3-none-any.whl
    ✓ idna==3.10  →  idna-3.10-py3-none-any.whl

  requests==2.32.5  63.2 KB  (total: 834.8 KB)
  ├── urllib3==2.3.0  341.8 KB
  ├── charset-normalizer==3.4.1  204.8 KB
  ├── certifi==2025.1.31  164.0 KB
  └── idna==3.10  61.4 KB
```

Every node shows its own size. Nodes that have sub-dependencies additionally show `(total: ...)` so you can see the weight of an entire sub-tree at a glance.

---

## All Options

```
pip-size [package] [options]

positional arguments:
  package               a requirement ("requests", "requests==2.31.0", "requests[security]")
                        or the path to a local .whl file (optionally "x.whl[extra]")

dependency options:
  --no-deps             Show size of the package itself only, no dependency resolution.
  -a, --all-extras      Include ALL optional (extra-gated) dependencies across the
                        entire tree. Each one is labelled [extra: <n>] in the output.
  --extras PATTERNS     Activate specific extras for the ROOT package only.
                        Comma-separated, supports glob wildcards:
                          --extras test
                          --extras 'test,dev'
                          --extras 'dev*'
                          --extras '*'
                        Can be combined with --all-extras.

skipping options:
  --skip NAMES          Package names or glob patterns to skip (comma-separated, repeatable).
                          --skip numpy,pandas --skip 'nvidia-*'
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

---

## Extras

### Explicit extras in the package spec

Works exactly like pip:

```bash
pip-size "requests[security]"
pip-size "fastapi[standard]"
```

Only the extras you name are activated — their sub-dependencies are resolved, but those sub-packages' own optional deps are not pulled in unless you also pass `--all-extras`.

### `--extras` — glob patterns for the root package

When you don't want to type the full bracket syntax, or when you want wildcards:

```bash
pip-size requests --extras security
pip-size mypackage --extras 'test,dev'
pip-size mypackage --extras 'dev*'   # dev, dev-tools, development, ...
pip-size mypackage --extras '*'      # every declared extra
```

### `-a` / `--all-extras` — every optional dep in the whole tree

```bash
pip-size requests -a
```

Walks the entire dependency tree and includes every optional dependency at every level, labelling each with `[extra: <n>]`. Useful for a worst-case size estimate.

Can be combined with `--extras`:

```bash
# Activate 'test' on the root, AND include all optionals everywhere else
pip-size mypackage --extras test --all-extras
```

---

## Local wheels

Point `pip-size` at a `.whl` file instead of a package name. The size is the file's size on
disk, the dependency list is read from `*.dist-info/METADATA` inside the wheel, and everything
below that is resolved from PyPI as usual:

```bash
pip-size ./dist/mypkg-1.0-py3-none-any.whl
pip-size "./dist/mypkg-1.0-py3-none-any.whl[server]"   # activate an extra, like pkg[server]
pip-size ./dist/mypkg-1.0-py3-none-any.whl --skip-installed
```

Handy as a pre-release check: "how much will users actually download when they install this?"

---

## Skipping dependencies

Some dependencies cost nothing to *your* users because they are already there (numpy on a
data-science box, torch on a GPU image). Skipped packages are **still listed in the tree with
their size**, tagged `[skipped: ...]`, but they are not added to the total and their own
dependencies are not expanded:

```
$ pip-size requests --skip idna,cert*

  requests==2.34.2  71.4 KB  (total: 455.5 KB)
  ├── certifi==2026.10.23  131.9 KB  [skipped: skip list]
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB  [skipped: skip list]
  └── urllib3==2.8.0  132.5 KB

  Total to download : 455.5 KB
  Skipped           : 199.9 KB  (2 package(s), not counted)
```

| Option | Skips |
|---|---|
| `--skip numpy,'nvidia-*'` | exactly these names / glob patterns (names are normalised: `Typing_Extensions` = `typing-extensions`) |
| `--skip-installed` | whatever is installed in the current environment |
| `--installed-from /opt/venv/bin/python` | whatever is installed in *that* interpreter's environment |
| `--skip-file freeze.txt` | every package in a `pip freeze` / requirements file; `-` reads stdin, so `pip freeze \| pip-size pkg --skip-file -` works, also for a freeze taken on another machine |

Installed / frozen packages are only skipped when their version **satisfies the requirement**
(numpy 1.26 installed but `numpy>=2` required → pip would download it, so it is still counted).
The root package is never skipped. The size shown for a skipped package is the file pip would
pick for the requirement, not necessarily the installed version.

---

## Python API

```python
import pip_size

res = pip_size.resolve("requests")                        # sync;  `await pip_size.resolve_async(...)` in async code
res = pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")
res = pip_size.resolve("fastapi", skip=["numpy", "nvidia-*"], skip_installed=True)

res.total_size            # bytes to download (skipped packages not counted)
res.skipped_size          # bytes of the skipped packages
res.root                  # PackageInfo tree: .name .version .size .filename .skipped .dependencies
res.packages              # the whole tree flattened, root first
res.skipped               # only the skipped ones
res.conflicts             # version conflicts found in the tree
res.warnings              # dependencies that could not be resolved (non-fatal)
res.to_dict()             # plain data, all sizes in bytes

for pkg in res.packages:
    print(pkg.name, pkg.version, pkg.size, pkg.skipped or "")
```

`resolve()` prints nothing and returns data. Pass `progress=print` to see resolver progress.
It raises `pip_size.ResolutionError` (or `WheelError` / `SkipSourceError`, all subclasses of
`PipSizeError`) when the root package cannot be resolved, and accepts the same options as the
CLI: `no_deps`, `all_extras`, `extras`, `skip`, `skip_installed`, `skip_files`,
`installed_from`, `use_cache`, `proxy`.

The CLI exit code is `1` when the package cannot be resolved, so it is safe to use in CI:

```bash
test "$(pip-size mypkg --quiet --bytes --skip-installed)" -lt 5000000
```

---

## Proxy Support

```bash
# HTTP proxy
pip-size requests --proxy http://user:pass@host:8080

# SOCKS5 proxy  (pip install aiohttp-socks)
pip-size requests --proxy socks5://host:1080

# Via environment variable (--proxy flag takes precedence)
HTTP_PROXY=http://host:8080 pip-size requests
ALL_PROXY=socks5://host:1080 pip-size requests
```

---

## Caching

API responses are cached locally for 24 hours to avoid repeated requests to PyPI.

| Platform | Cache location |
|---|---|
| Linux / macOS | `~/.cache/pip-size/` (respects `$XDG_CACHE_HOME`) |
| Windows | `%LOCALAPPDATA%\pip-size\Cache\` |

```bash
pip-size --clear-cache          # wipe the entire cache
pip-size requests --no-cache    # skip cache for this run only
```

---

## JSON Output

```bash
$ pip-size requests --json
```

```json
{
  "name": "requests",
  "version": "2.32.5",
  "size": "63.2 KB",
  "total_size": "834.8 KB",
  "filename": "requests-2.32.5-py3-none-any.whl",
  "dependencies": [
    {
      "name": "urllib3",
      "version": "2.3.0",
      "size": "341.8 KB",
      "total_size": "341.8 KB",
      "filename": "urllib3-2.3.0-py3-none-any.whl"
    },
    {
      "name": "certifi",
      "version": "2025.1.31",
      "size": "164.0 KB",
      "total_size": "164.0 KB",
      "filename": "certifi-2025.1.31-py3-none-any.whl"
    }
  ]
}
```

Every node also carries `size_bytes` and `total_size_bytes` (integers). Skipped packages carry
`"skipped": "<reason>"`, a local wheel carries `"local": true`, and the root node adds
`skipped_size` / `skipped_size_bytes` / `skipped_count` when something was skipped, plus
`conflicts` / `warnings` when there are any. With `--json` (and `--quiet`) nothing but the
JSON document is written to stdout.

---

## How It Works

1. Lists every release of a project from the PyPI Simple API (`/simple/<name>/`, PEP 691 JSON) — file names and sizes, no package download. A local `.whl` is read from disk instead.
2. Resolves the version specifier: highest matching version that has a file usable on your interpreter and platform; yanked files are ignored, pre-releases only when asked for or unavoidable
3. Selects the best wheel for your platform, mirroring pip's priority order
4. Reads `requires_dist` of the chosen release (`/pypi/<name>/<version>/json`) and resolves the dependency graph in BFS layers — each layer is fetched concurrently
5. Evaluates environment markers (Python version, OS, extras) to include only relevant deps
6. Applies skip rules, then caches every API response locally with a 24-hour TTL

### Limitations

- Each package is resolved **once**, against the first requirement that mentions it (not a full pip-style backtracking resolver). If another requirement in the tree rejects that version, `pip-size` reports a version conflict instead of silently ignoring it. A package shared by several parents appears once, under the first.
- Extras requested on a dependency that was already resolved without them are not merged in.
- Wheel and marker selection use the interpreter that runs `pip-size`.

---

## License

`pip-size` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

---

> **Note:** `pip-size` provides estimates based on PyPI metadata. Actual installed sizes may differ slightly due to compression and platform-specific factors.
