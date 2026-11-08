# Skipping dependencies

Some dependencies cost your users nothing because they are already installed: numpy on a data
science box, torch on a GPU image, everything in your base container. Skipping lets you size a
package against that.

A skipped package **stays in the tree with its size and a reason**, but it is not counted in the
total and its own dependencies are not expanded.

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

## Ways to say what is already there

| Option | Skips |
| --- | --- |
| `--skip numpy,"nvidia-*"` | These names and glob patterns. Repeatable. Names are normalised, so `Typing_Extensions` equals `typing-extensions`. |
| `--skip-installed` | Everything installed in the environment that runs `pip-size`. |
| `--installed-from /opt/venv/bin/python` | Everything installed in that interpreter's environment. Implies `--skip-installed`. |
| `--skip-file freeze.txt` | Every package in a `pip freeze` or requirements file. `-` reads stdin. |

The options can be combined. In the tree, the reason is shown next to the package:

| Reason | Source |
| --- | --- |
| `skip list` | `--skip` |
| `installed 1.26.4` | An installed environment or a freeze file, with the version found there. |
| `already installed` | A freeze file entry without an exact version (`name`, or `name @ url`). |

## An installed package must satisfy the requirement

Installed and frozen packages are skipped **only if their version satisfies the requirement** that
brought them into the tree. If numpy 1.26 is installed but a package needs `numpy>=2`, pip would
download a new numpy, so it still counts.

```bash
printf 'certifi==2026.10.23\nurllib3==1.0\n' | pip-size requests --skip-file -
```

```text
  requests==2.34.2  71.4 KB  (total: 523.4 KB)
  ├── certifi==2026.10.23  131.9 KB  [skipped: installed 2026.10.23]
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  └── urllib3==2.8.0  132.5 KB

  Total to download : 523.4 KB
  Skipped           : 131.9 KB  (1 package(s), not counted)
```

Here the frozen `urllib3==1.0` does not satisfy what `requests` asks for, so urllib3 is counted.

## Details worth knowing

- **The root package is never skipped.**
- **Children of a skipped package are not expanded.** If `numpy` is skipped, whatever numpy depends on is not listed or counted either; it is assumed to be there with it.
- **The size shown for a skipped package** is the file pip would pick for the requirement, not necessarily the installed version.
- **Skipping applies to local wheels too.** The dependencies of a [local wheel](local-wheels.md) can be skipped like any other.

## Sizing against a base image or another machine

Take a freeze where the packages actually are, then size against it anywhere:

```bash
docker run --rm my-base-image pip freeze > base-freeze.txt
pip-size fastapi --skip-file base-freeze.txt
```

Or inspect another environment directly:

```bash
pip-size fastapi --installed-from /srv/app/.venv/bin/python
```

!!! warning "Isolated installs of pip-size"
    `--skip-installed` inspects the environment that runs `pip-size`. If you installed it with
    `pipx` or `uvx`, that is a private environment that does not contain your project's packages.
    Use `--installed-from` or `--skip-file` instead.

## From Python

```python
res = pip_size.resolve("requests", skip=["idna"], skip_installed=True)
res.skipped_size
```

See [Skip rules](../python-api/skip-rules.md).
