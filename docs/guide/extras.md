# Extras

Packages can declare optional dependencies, called *extras* (`requests[socks]`). By default
`pip-size` includes only the dependencies that are always installed. There are four ways to add
extras, from narrow to wide:

| You write | Activates |
| --- | --- |
| `pip-size "requests[socks]"` | The extras named in the requirement, on the root package. Exactly like `pip install`. |
| `pip-size requests --extras socks` | The same, through a flag. Accepts comma-separated names and glob patterns. |
| `pip-size requests -a` | **Every** extra the root package declares: its worst-case install. |
| `pip-size requests --all-extras-deep` | Every extra of every package in the tree. Unbounded, see below. |

## Naming extras

```bash
pip-size "requests[socks]"
pip-size requests --extras socks
pip-size mypkg --extras "test,dev"      # two extras
pip-size mypkg --extras "dev*"          # glob: dev, dev-docs, ...
pip-size mypkg --extras "*"             # every extra of the root package
```

`--extras` can be combined with the `[...]` form and with `-a`. A pattern that matches no extra is
ignored.

## Reading the result

A package that is in the tree because of an extra is labelled with it:

```text
$ pip-size "requests[socks]"

  requests==2.34.2  71.4 KB  (total: 671.7 KB)
  ├── certifi==2026.10.23  131.9 KB
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  ├── PySocks==1.7.1  16.3 KB  [extra: socks]
  └── urllib3==2.8.0  132.5 KB
```

## Worst case for one package

`-a` is the answer to "what is the most this package can cost?":

```text
$ pip-size requests -a

  requests==2.34.2  71.4 KB  (total: 2.2 MB)
  ├── certifi==2026.10.23  131.9 KB
  ├── chardet==7.7.0  1.6 MB  [extra: use-chardet-on-py3]
  ├── charset_normalizer==3.5.2  251.6 KB
  ├── idna==3.20  68.0 KB
  ├── PySocks==1.7.1  16.3 KB  [extra: socks]
  └── urllib3==2.8.0  132.5 KB
```

Extras of *dependencies* are activated only when their parent asks for them (`httpx[http2]`
inside another package's requirements), which is how pip behaves.

## Every extra, everywhere

`--all-extras-deep` activates every extra of every package at every level, test and dev extras
included.

!!! warning "Unbounded"
    When this page was written, `pip-size requests --all-extras-deep` resolved about 7,000
    packages and reported 25.1 GB. Use `-a` or `--extras` for numbers you can act on. The deep
    mode exists for completeness.

## Markers still apply

Environment markers are evaluated whatever the extras: a dependency declared only for another
operating system or Python version is left out even when its extra is active.
