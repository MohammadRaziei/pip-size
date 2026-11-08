# resolve()

```text
resolve(spec, *, no_deps=False, all_extras=False, all_extras_deep=False, extras=None,
        skip=(), skip_installed=False, skip_files=(), installed_from=None, skip_rules=None,
        use_cache=True, proxy=None, progress=None) -> Resolution

async resolve_async(...)   same arguments, awaitable
```

Resolves a package and its dependency tree and returns a [`Resolution`](results.md#resolution).
Nothing is printed unless you pass `progress`.

## spec

| Form | Example |
| --- | --- |
| A requirement string | `"requests"`, `"requests==2.31.0"`, `"requests[socks]"`, `"httpx>=0.27,<1"` |
| A `packaging.requirements.Requirement` | `Requirement("requests[socks]")` |
| A path to a local wheel (`str` or `os.PathLike`), optionally with extras | `"./dist/mypkg-1.0-py3-none-any.whl"`, `"./dist/mypkg-1.0-py3-none-any.whl[server]"` |
| A source to build a wheel from first: a project directory, an sdist archive, a VCS URL or a direct reference | `"./my-project"`, `"git+https://github.com/owner/repo"`, `"mypkg[x] @ git+https://..."`. See [Directories, git URLs and archives](../guide/source-builds.md). |

## Options

All options are keyword-only.

| Option | Default | Meaning |
| --- | --- | --- |
| `no_deps` | `False` | Only the package itself, no dependency resolution. |
| `extras` | `None` | Extras to activate on the **root** package: a string (`"test,dev*"`) or an iterable of names or glob patterns. Same as `pkg[extra]`. See [Extras](../guide/extras.md). |
| `all_extras` | `False` | Activate every extra declared by the root package. |
| `all_extras_deep` | `False` | Also activate every extra of every dependency. Unbounded: can resolve thousands of packages. |
| `skip` | `()` | Names or glob patterns to keep in the tree but not count: a string (`"numpy,nvidia-*"`) or an iterable. |
| `skip_installed` | `False` | Also skip packages installed in this environment, when the installed version satisfies the requirement. |
| `skip_files` | `()` | Paths of `pip freeze` or requirements files whose packages are skipped. `"-"` reads stdin. |
| `installed_from` | `None` | Path of another Python executable whose environment `skip_installed` inspects. Implies `skip_installed`. |
| `skip_rules` | `None` | A prebuilt [`SkipRules`](skip-rules.md). When given, the four skip options above are ignored. |
| `use_cache` | `True` | Use the 24-hour on-disk response cache. |
| `proxy` | `None` | Proxy URL (`http://...`, `socks5://...`). Falls back to the proxy environment variables. See [Proxies and cache](../guide/network.md). |
| `progress` | `None` | Callback that receives one line of text per resolved package. |

## Returns and raises

**Returns** a [`Resolution`](results.md#resolution).

**Raises** a subclass of [`PipSizeError`](errors.md) when the root package cannot be resolved. A
failure on a dependency is not an exception: the rest of the tree is returned and the problem is
added to `Resolution.warnings`.

## Examples

```python
pip_size.resolve("requests", no_deps=True).total_size
pip_size.resolve("requests", extras="socks").total_size
pip_size.resolve("requests", all_extras=True).total_size
pip_size.resolve("fastapi", skip=["numpy", "nvidia-*"], skip_installed=True)
pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")
pip_size.resolve("requests", proxy="http://host:8080", use_cache=False)
```

## Progress

`progress` receives the same lines the command line prints while it works:

```python
lines = []
pip_size.resolve("idna", progress=lines.append)
lines    # ['  ✓ idna==3.20  →  idna-3.20-py3-none-any.whl']
```
