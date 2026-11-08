# Quick start

## Your first size

```bash
pip-size requests
```

While it works, `pip-size` prints one line per resolved package, then the tree:

```text
🔍 Resolving 'requests'...
  ✓ requests==2.34.2  →  requests-2.34.2-py3-none-any.whl
    ✓ charset_normalizer==3.5.2  →  charset_normalizer-3.5.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl
    ✓ idna==3.20  →  idna-3.20-py3-none-any.whl
    ✓ urllib3==2.8.0  →  urllib3-2.8.0-py3-none-any.whl
    ✓ certifi==2026.10.23  →  certifi-2026.10.23-py3-none-any.whl

  requests==2.34.2  71.4 KB  (total: 655.3 KB)
  ├── certifi==2026.10.23  131.9 KB
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  └── urllib3==2.8.0  132.5 KB
```

The `→` lines show the file chosen for each package: the wheel that matches your Python version
and platform, as pip would pick it. Nothing is downloaded; names and sizes come from PyPI metadata.

### Reading the tree

| Part | Meaning |
| --- | --- |
| `71.4 KB` | The size of that package's own file. |
| `(total: 655.3 KB)` | Shown on nodes that have dependencies: the node plus everything below it. The root's total is what a user downloads. |
| `[extra: socks]` | The package is there because of an [extra](guide/extras.md). |
| `[skipped: ...]` | The package is shown but not counted, see [Skipping dependencies](guide/skipping.md). |
| `[local wheel]` | The root is a `.whl` read from disk, see [Local wheels](guide/local-wheels.md). |

## Common tasks

```bash
# The package alone, no dependencies
pip-size requests --no-deps

# A specific version, or extras, written like pip does
pip-size "requests==2.31.0"
pip-size "requests[socks]"

# A wheel you just built
pip-size ./dist/mypkg-1.0-py3-none-any.whl

# Do not count what the target machine already has
pip-size fastapi --skip-installed
pip-size fastapi --skip numpy,pandas

# Only a number, for scripts
pip-size requests --quiet            # 655.3 KB
pip-size requests --quiet --bytes    # 671058

# The tree as JSON
pip-size requests --json
```

!!! tip "Quote brackets and globs"
    In zsh and some other shells, `requests[socks]` and `'nvidia-*'` are expanded by the shell
    before `pip-size` sees them. Put them in quotes.

## From Python

```python
import pip_size

res = pip_size.resolve("requests")
res.total_size            # 671058
```

See the [Python API](python-api/index.md).

## Where next

- [Command line](cli.md): every option and the exit codes.
- [Skipping dependencies](guide/skipping.md): size a package against what is already installed.
- [Use in CI](guide/ci.md): fail a build when an install gets too big.
- [Limitations](concepts/limitations.md): what the numbers are, and are not.
