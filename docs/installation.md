# Installation

`pip-size` needs **Python 3.9 or newer**. Its only dependencies are `aiohttp` and `packaging`.

=== "pip"

    ```bash
    pip install pip-size
    ```

=== "pipx"

    ```bash
    pipx install pip-size
    ```

=== "uv"

    ```bash
    uv tool install pip-size     # or run without installing: uvx pip-size requests
    ```

The same install provides the `pip-size` command and the `pip_size` Python module.

```bash
pip-size --version
```

```text
pip-size 0.3.0
```

## Optional extras

| Install | Adds |
| --- | --- |
| `pip install "pip-size[socks]"` | SOCKS4 and SOCKS5 proxy support, through `aiohttp-socks`. |

## Isolated installs and `--skip-installed`

`pipx install` and `uvx` put `pip-size` in its own virtual environment. `--skip-installed` looks at
the environment that **runs** `pip-size`, so in an isolated install it sees only `pip-size`'s own
dependencies, not your project's packages. Point it at the right environment instead:

```bash
pip-size fastapi --installed-from /path/to/project/.venv/bin/python
```

See [Skipping dependencies](guide/skipping.md) for every way to say what is already installed.

## From source

```bash
git clone https://github.com/MohammadRaziei/pip-size
cd pip-size
pip install -e .
```
