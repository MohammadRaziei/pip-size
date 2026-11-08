# Result objects

## Resolution

What `resolve()` returns.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `root` | `PackageInfo` | The package you asked about, with its dependencies nested below it. |
| `total_size` | `int` | Bytes to download: the whole tree, **skipped packages not counted**. |
| `skipped_size` | `int` | Bytes of the skipped packages. |
| `packages` | `list[PackageInfo]` | The whole tree flattened, root first. Skipped packages included. |
| `skipped` | `list[PackageInfo]` | Only the skipped packages. |
| `conflicts` | `list[Conflict]` | Packages resolved to a version that another requirement in the tree rejects. |
| `warnings` | `list[str]` | Non-fatal problems, such as a dependency that could not be found. |
| `to_dict()` | `dict` | Plain data for `json.dumps`. All sizes are integers in bytes. |

```python
pip_size.resolve("idna").to_dict()
# {'name': 'idna', 'version': '3.20', 'size': 69583, 'total_size': 69583,
#  'filename': 'idna-3.20-py3-none-any.whl', 'skipped_size': 0, 'conflicts': [], 'warnings': []}
```

Nodes with dependencies add a `dependencies` list; `via_extra`, `skipped` and `local` appear on the
nodes they apply to. This differs from [`pip-size --json`](../guide/output.md#json), which formats
sizes as text and adds `*_bytes` fields: `to_dict()` always uses integers.

## PackageInfo

One node of the dependency tree.

| Field | Type | Meaning |
| --- | --- | --- |
| `name`, `version` | `str` | Package name and the selected version. |
| `size` | `int` | Bytes of the distribution file pip would download (a wheel if possible, otherwise an sdist). `0` with `filename == "N/A"` if the version has no file usable on this platform. |
| `filename` | `str` | That file's name. |
| `dependencies` | `list[PackageInfo]` | Direct dependencies, sorted by name. A package that several parents depend on appears once, under the first. |
| `via_extra` | `str \| None` | The extra that pulled this package in, if any. |
| `skipped` | `str \| None` | Why it is skipped (`"skip list"`, `"installed 1.26.4"`, ...), or `None`. |
| `local` | `bool` | `True` for a wheel read from disk. |

| Method | Returns |
| --- | --- |
| `total_size(include_skipped=False)` | This node plus everything below it, in bytes. |
| `skipped_size()` | Bytes of skipped packages in this sub-tree. |
| `walk()` | Iterator over this node and all descendants, depth first. |
| `to_dict()` | Plain data for this sub-tree. |

```python
res = pip_size.resolve("requests", skip=["idna"])

res.total_size                                   # 601475, without idna
res.skipped_size                                 # 69583
[(p.name, p.skipped) for p in res.skipped]       # [('idna', 'skip list')]
res.root.total_size(include_skipped=True)        # 671058, with it
```

## Conflict

Each package is resolved once, against the first requirement that mentions it. If another
requirement in the tree rejects that version, you get a `Conflict` instead of silence.

| Field | Meaning |
| --- | --- |
| `name`, `version` | The package and the version that was selected. |
| `requirement` | The specifier that rejects it, for example `"<2"`. |
| `required_by` | The package that asked for it, or `None` for the request itself. |

## Formatting a result

`Printer` formats a `Resolution` the way the command line does, and `Printer.format_size` turns
bytes into text:

```python
from pip_size import Printer

res = pip_size.resolve("idna")
Printer().print_tree(res)                    # the tree
Printer().print_json(res)                    # the CLI's --json document
Printer(as_bytes=True).print_quiet(res)      # 69583
Printer.format_size(69583)                   # '68.0 KB'
```

`PipSize` is the command-line behaviour as a class (resolve, then print): `PipSize(no_deps=True).run("requests")`
returns `True` if the package was resolved. It is what the `pip-size` command calls. In programs,
prefer [`resolve()`](resolve.md).
