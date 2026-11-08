# Output formats

| Flag | Output on stdout |
| --- | --- |
| *(none)* | Progress lines, then the tree. Meant for people. |
| `--quiet` | The total size, one line. |
| `--quiet --bytes` | The total size in bytes, one line. |
| `--json` | One JSON document. Nothing else. |

`--json` and `--quiet` suppress the header and the progress lines, so stdout can be piped straight
into another program. Errors always go to stderr.

## The tree

```text
  requests==2.34.2  71.4 KB  (total: 655.3 KB)
  ├── certifi==2026.10.23  131.9 KB
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  └── urllib3==2.8.0  132.5 KB
```

Dependencies are sorted by name. A package that several packages depend on appears once, under
the first. Tags after a size: `[extra: ...]`, `[skipped: ...]`, `[local wheel]`, `[built from source]`.

When something was skipped, a summary follows the tree:

```text
  Total to download : 455.5 KB
  Skipped           : 199.9 KB  (2 package(s), not counted)
```

### Conflicts and warnings

If a package in the tree is selected at a version that another requirement rejects, the tree is
followed by a conflict notice (illustrative names):

```text
  app==1.0  11.7 KB  (total: 959.0 KB)
  ├── lib-a==1.0  29.3 KB  (total: 908.2 KB)
  │   └── shared==2.0  878.9 KB
  └── lib-b==1.0  39.1 KB

  ⚠ Version conflicts (each package is resolved once, against the first requirement seen):
    - shared==2.0 selected, but lib-b requires shared<2
```

A dependency that could not be resolved is listed under `⚠ Warnings`. Neither case changes the
exit status. See [Limitations](../concepts/limitations.md).

## `--quiet` and `--bytes`

```bash
pip-size requests --quiet            # 655.3 KB
pip-size requests --quiet --bytes    # 671058
```

The total never includes [skipped](skipping.md) packages.

## JSON (`--json`) {#json}

```bash
pip-size idna --json
```

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

### Fields of every node

| Field | Type | Meaning |
| --- | --- | --- |
| `name`, `version` | string | Package and selected version. |
| `size`, `size_bytes` | string, integer | The package's own file. `size` is formatted (or raw bytes with `--bytes`). |
| `total_size`, `total_size_bytes` | string, integer | The node and everything below it, skipped packages not counted. |
| `filename` | string | The distribution file chosen. `"N/A"` if no file fits this platform. |
| `dependencies` | array | Child nodes. Absent for leaves. |
| `via_extra` | string | The extra that pulled the package in. Only if applicable. |
| `skipped` | string | The reason it is skipped. Only if applicable. |
| `local` | boolean | `true` for a local wheel or one built from source. Only if applicable. |
| `built_from` | string | The directory, URL or archive the wheel was built from. Only if applicable. |

### Extra fields on the root node

| Field | When present |
| --- | --- |
| `skipped_size`, `skipped_size_bytes`, `skipped_count` | Something was skipped. |
| `conflicts` | There are version conflicts: a list of `{name, version, requirement, required_by}`. |
| `warnings` | Some dependency could not be resolved: a list of strings. |

!!! tip "Integers for programs"
    Use `size_bytes` and `total_size_bytes` in scripts; the formatted strings are for humans. The
    Python API's `to_dict()` uses integers throughout, see [Result objects](../python-api/results.md).

### Using it from a shell

```bash
pip-size requests --json | python -c "import sys, json; print(json.load(sys.stdin)['total_size_bytes'])"
pip-size requests --json | jq '.total_size_bytes'          # if you have jq
```
