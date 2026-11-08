# Wheels

To size a wheel together with its dependencies, pass its path to [`resolve()`](resolve.md); see
[Local wheels](../guide/local-wheels.md).

```python
res = pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")
res.root.local          # True
res.root.size           # size of the file itself
```

## read_wheel

To look at a wheel without resolving anything, use `read_wheel`. It opens the file as a zip and
parses `*.dist-info/METADATA`:

```python
w = pip_size.read_wheel("./dist/pip_size-0.3.0-py3-none-any.whl")

w.name, w.version                # ('pip-size', '0.3.0')
w.size                           # bytes of the .whl file
w.requires_dist                  # ['aiohttp>=3.8.0', 'packaging', "aiohttp-socks; extra == 'socks'"]
w.requires_python                # '>=3.9'
w.provides_extra                 # ['socks']
```

It returns a `LocalWheel`:

| Field | Type | Meaning |
| --- | --- | --- |
| `path` | `Path` | The file. |
| `name`, `version` | `str` | From `METADATA`; the file name is used if they are missing there. |
| `size` | `int` | Size of the `.whl` file in bytes. |
| `requires_dist` | `list[str]` | The dependencies, as written in the metadata. |
| `requires_python` | `str \| None` | The `Requires-Python` specifier. |
| `provides_extra` | `list[str]` | The extras the wheel declares. |
| `filename` | `str` | Property: the file name. |

A missing file, a file that is not a zip, or a wheel without `METADATA` raises
[`WheelError`](errors.md).
