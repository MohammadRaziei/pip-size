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

**Dependencies:**

```bash
pip install aiohttp packaging          # required
pip install aiohttp-socks              # optional — only needed for SOCKS proxy support
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
  package               e.g. "requests" or "requests==2.31.0"

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

---

## How It Works

1. Fetches `https://pypi.org/pypi/{package}/json` — no actual package download
2. Resolves version specifiers against the release list
3. Selects the best wheel for your platform, mirroring pip's priority order
4. Reads `requires_dist` metadata and resolves the dependency graph in BFS layers — each layer is fetched concurrently
5. Evaluates environment markers (Python version, OS, extras) to include only relevant deps
6. Caches every API response locally with a 24-hour TTL

---

## License

`pip-size` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

---

> **Note:** `pip-size` provides estimates based on PyPI metadata. Actual installed sizes may differ slightly due to compression and platform-specific factors.