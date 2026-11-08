# Local wheels

Give `pip-size` the path to a `.whl` instead of a package name:

```bash
pip-size ./dist/mypkg-1.0-py3-none-any.whl
pip-size "./dist/mypkg-1.0-py3-none-any.whl[server]"    # an extra, like mypkg[server]
pip-size ./dist/mypkg-*.whl --skip-installed             # the glob must match exactly one file
```

It answers the question *"how much will users download when they install this?"* before the
package is on PyPI.

## What is read from the wheel

| Taken from | What |
| --- | --- |
| The file on disk | The size, which is the file's size in bytes. |
| `*.dist-info/METADATA` inside the wheel | `Name`, `Version`, `Requires-Dist` (the dependencies), `Requires-Python`, `Provides-Extra`. |

Everything below the root is resolved from PyPI exactly as for a normal package: markers and
extras are evaluated, [skip rules](skipping.md) apply, and the wheel file for each dependency is
chosen for your Python and platform.

```text
$ pip-size ./demo-1.0-py3-none-any.whl

  demo==1.0  420.0 B  (total: 200.3 KB)  [local wheel]
  ├── certifi==2026.10.23  131.9 KB
  └── idna==3.20  68.0 KB

$ pip-size "./demo-1.0-py3-none-any.whl[server]"

  demo==1.0  420.0 B  (total: 332.8 KB)  [local wheel]
  ├── certifi==2026.10.23  131.9 KB
  ├── idna==3.20  68.0 KB
  └── urllib3==2.8.0  132.5 KB  [extra: server]
```

## Errors

A problem with the file stops the run with exit status `1` and a message on stderr:

| Problem | Message contains |
| --- | --- |
| The path does not exist | `wheel not found` |
| The file is not a zip archive | `not a valid wheel (zip) file` |
| There is no `*.dist-info/METADATA` inside | `no *.dist-info/METADATA inside the wheel` |

## Limits

- Only wheels (`.whl`) are supported. A source distribution does not carry reliable dependency metadata, since it is usually produced by running a build.
- The dependencies listed in the wheel are looked up on PyPI by name. A dependency that exists only on a private index cannot be resolved and is reported as a warning.

## From Python

```python
res = pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")
res.root.local          # True
```

To read a wheel's metadata without resolving anything, see [Wheels](../python-api/wheels.md).
