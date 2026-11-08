# Skip rules

Skipping keeps a package in the tree, with its size and a reason, but leaves it out of
`total_size` and does not expand its dependencies. The concepts and caveats are in the
[Skipping guide](../guide/skipping.md); this page covers the API.

The simplest use is through the options of [`resolve()`](resolve.md):

```python
pip_size.resolve("requests", skip=["idna"])                       # names and globs
pip_size.resolve("requests", skip_installed=True)                 # this environment
pip_size.resolve("requests", installed_from="/opt/venv/bin/python")
pip_size.resolve("requests", skip_files=["frozen.txt"])           # a pip freeze file
```

## SkipRules

Build the rules once and reuse them, for example to avoid listing the installed packages on every
call:

```python
from pip_size import SkipRules

rules = SkipRules.build(names="nvidia-*", skip_installed=True)

for name in ["requests", "httpx"]:
    res = pip_size.resolve(name, skip_rules=rules)

rules.match("nvidia-cublas-cu12")    # 'skip list'
rules.match("zzz-not-installed")     # None
```

| Member | Meaning |
| --- | --- |
| `SkipRules(names=(), installed=None)` | `names`: a string or iterable of names and globs. `installed`: a mapping `{package: version_or_None}`; a `None` version means "present, version unknown" and always matches. |
| `SkipRules.build(names=(), skip_installed=False, freeze_files=(), python=None)` | Gathers the installed packages for you: from this environment, from the environment of `python`, and from freeze files. |
| `rules.match(name, specifier="")` | The reason the package is skipped (`"skip list"`, `"installed 1.26.4"`, `"already installed"`), or `None`. An installed version that does not satisfy `specifier` does not match. |
| `bool(rules)` | `False` for an empty rule set. |

Names are normalised (`Typing_Extensions` equals `typing-extensions`) and may contain glob patterns.
