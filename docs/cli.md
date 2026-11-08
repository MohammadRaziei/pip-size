# Command line

```text
pip-size [options] package
```

`package` is a requirement (`requests`, `requests==2.31.0`, `requests[socks]`, `httpx>=0.27,<1`)
or the path to a [local wheel](guide/local-wheels.md) (`./dist/mypkg-1.0-py3-none-any.whl`,
optionally with extras: `...whl[server]`).

## Options

### Dependencies

| Option | Meaning |
| --- | --- |
| `--no-deps` | Size of the package itself only, no dependency resolution. |
| `--extras PATTERNS` | Activate extras on the **root** package. Comma-separated names or glob patterns: `test`, `test,dev`, `dev*`, `*`. Same as `pkg[extra]`, with wildcards. |
| `-a`, `--all-extras` | Activate **every** extra declared by the root package: the worst-case install of that package. |
| `--all-extras-deep` | Also activate every extra of every dependency, at every level. Unbounded, see [Extras](guide/extras.md#every-extra-everywhere). |

### Skipping

| Option | Meaning |
| --- | --- |
| `--skip NAMES` | Package names or glob patterns to skip. Comma-separated; repeatable. |
| `--skip-installed` | Skip packages installed in the environment that runs `pip-size`, when their version satisfies the requirement. |
| `--installed-from PYTHON` | Like `--skip-installed`, for the environment of another interpreter. Implies `--skip-installed`. |
| `--skip-file FILE` | Skip every package in a `pip freeze` or requirements file. Repeatable. `-` reads stdin. |

Skipped packages stay in the tree with their size but are not counted. See
[Skipping dependencies](guide/skipping.md).

### Output

| Option | Meaning |
| --- | --- |
| `--quiet` | Print only the total size, and nothing else. |
| `--bytes` | Report sizes in bytes instead of `KB`, `MB`, and so on. |
| `--json` | Print the tree as JSON. Only the JSON goes to stdout. |

See [Output formats](guide/output.md).

### Network and cache

| Option | Meaning |
| --- | --- |
| `--proxy URL` | Proxy for all PyPI requests. Overrides the proxy environment variables. |
| `--no-cache` | Do not read or write the response cache for this run. |
| `--clear-cache` | Delete all cached responses and exit. |

See [Proxies and cache](guide/network.md).

### Logging and information

| Option | Meaning |
| --- | --- |
| `--verbose` | INFO logging. |
| `--extra-verbose` | DEBUG logging: HTTP requests, wheel scoring, marker evaluation, cache hits. |
| `--version` | Print the version and exit. |
| `-h`, `--help` | Show the help and exit. |

## Exit status

| Code | Meaning |
| --- | --- |
| `0` | The package was resolved and its size printed (or `--clear-cache` / `--version` / `--help` ran). |
| `1` | The package could not be resolved, a local wheel or skip file could not be read, or a similar error. |
| `2` | The command line is invalid, for example no package was given. |

A dependency that cannot be resolved does **not** change the exit status; it is reported as a
warning and the rest of the tree is still printed.

## Standard streams

| Stream | Contents |
| --- | --- |
| stdout | The result. With `--json` or `--quiet`, nothing else. |
| stderr | Errors, such as `❌ Could not resolve ...`. Logging output (`--verbose`) goes to stderr as well. |

Without `--json` or `--quiet`, the progress lines (`✓ name==version → file`) and the tree both go
to stdout.

## Environment variables

| Variable | Effect |
| --- | --- |
| `HTTPS_PROXY`, `HTTP_PROXY` | Proxy for PyPI requests. |
| `ALL_PROXY`, `SOCKS_PROXY` | Proxy used if the two above are not set. |
| `XDG_CACHE_HOME` | Location of the cache on Linux and macOS. |
| `LOCALAPPDATA` | Location of the cache on Windows. |

Lowercase spellings (`https_proxy`, ...) work too.

## Examples

```bash
pip-size "requests[socks]" --skip idna        # extras and skipping together
pip-size mypkg --extras "test,dev*" --no-deps  # the package alone, with extras named
pip-size ./dist/mypkg-*.whl --skip-installed   # the glob must match exactly one file
pip freeze | pip-size fastapi --skip-file -    # size against a freeze from stdin
pip-size fastapi --json | python -m json.tool  # pretty-print the JSON
```

<details>
<summary>Full <code>--help</code> output</summary>

```text
usage: pip-size [-h] [--version] [--no-deps] [-a] [--all-extras-deep] [--extras PATTERNS]
                [--skip NAMES] [--skip-installed] [--installed-from PYTHON] [--skip-file FILE]
                [--proxy URL] [--no-cache] [--clear-cache] [--quiet] [--bytes] [--json]
                [--verbose | --extra-verbose]
                [package]

Calculate real download size of a PyPI package. Zero downloads.

positional arguments:
  package               a requirement ("requests", "requests==2.31.0", "requests[security]")
                        or the path to a local .whl file (optionally "x.whl[extra]")

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --no-deps             Show size of the package itself only, without resolving dependencies.
  -a, --all-extras      Activate EVERY extra declared by the root package (worst-case install
                        of this package). Extras of dependencies are activated only when their
                        parent requests them, like pip. Each extra-gated package is labelled
                        [extra: <name>].
  --all-extras-deep     Like --all-extras, but also activate every extra of every dependency
                        at every level. Unbounded: test/dev extras are included, so the tree
                        can reach thousands of packages.
  --extras PATTERNS     Comma-separated names or glob patterns of extras to activate on the
                        ROOT package only, like pkg[extra1,extra2] but with wildcards.
                        Examples: 'test', 'test,dev', 'dev*', '*' (every extra). Can be
                        combined with --all-extras.
  --proxy URL           Proxy URL for all PyPI requests; overrides the HTTPS_PROXY /
                        HTTP_PROXY / ALL_PROXY environment variables. Examples:
                        http://user:pass@host:8080, socks5://host:1080 (SOCKS needs: pip
                        install 'pip-size[socks]').
  --no-cache            Bypass cache and always fetch fresh data from PyPI.
  --clear-cache         Delete all cached PyPI responses and exit.
  --quiet               Print only the total size and nothing else.
  --bytes               Report all sizes in raw bytes instead of human-readable units.
  --json                Output the full dependency tree as JSON.
  --verbose             Enable INFO logging.
  --extra-verbose       Enable DEBUG logging (HTTP, wheel scoring, marker evaluation, cache).

skipping:
  Skipped dependencies stay in the tree with their size and are tagged [skipped: ...], but
  are not added to the total and their own dependencies are not expanded.

  --skip NAMES          Comma-separated package names or glob patterns to skip (repeatable),
                        e.g. numpy,'nvidia-*'.
  --skip-installed      Skip packages already installed in this environment, when the
                        installed version satisfies the requirement.
  --installed-from PYTHON
                        Like --skip-installed, but inspect the environment of this python
                        executable (e.g. /opt/venv/bin/python). Implies --skip-installed.
  --skip-file FILE      Skip every package listed in a pip-freeze / requirements file
                        (repeatable). Use '-' to read the list from stdin.
```

</details>
