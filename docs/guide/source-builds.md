# Directories, git URLs and archives

Besides package names and `.whl` files, `pip-size` accepts a **source**: a project directory, a
VCS URL, or an sdist archive. It first **builds a wheel** from it, then sizes that wheel exactly
like a [local wheel](local-wheels.md): the wheel's size is the root, its dependencies come from
the wheel's metadata and are resolved from PyPI.

```bash
pip-size ./my-project                              # a directory with pyproject.toml or setup.py
pip-size "./my-project[server]"                    # ... with an extra
pip-size "git+https://github.com/owner/repo"       # a VCS URL (git, hg, svn, bzr)
pip-size "git+https://github.com/owner/repo@v1.2#subdirectory=pkg"
pip-size "mypkg[server] @ git+https://github.com/owner/repo"   # direct reference
pip-size ./dist/mypkg-1.0.tar.gz                   # an sdist archive
```

## What you get

```bash
pip-size . --skip aiohttp
```

```text
  ⚙ building a wheel from . ...

  pip-size==0.3.1  31.5 KB  (total: 158.4 KB)  [built from source]
  ├── aiohttp==3.14.5  1.8 MB  [skipped: skip list]
  └── packaging==26.3  126.9 KB

  Total to download : 158.4 KB
  Skipped           : 1.8 MB  (1 package(s), not counted)
```

The root is tagged `[built from source]`. Every other option works as usual: `--no-deps`,
`--extras`, [`--skip`](skipping.md), `--json` and the rest.

## How the spec is recognised

| You write | Treated as |
| --- | --- |
| `./dir`, `../dir`, `/abs/dir`, `~/dir`, `.` | A directory. It must contain `pyproject.toml` or `setup.py`. |
| `git+https://...`, `hg+...`, `svn+...`, `bzr+...` | A VCS URL. |
| `name @ url`, `name[extra] @ url` | A direct reference to a URL (VCS, sdist, or wheel). |
| A path ending in `.tar.gz`, `.zip`, ... | An sdist archive. |
| `requests`, `requests[socks]`, `requests>=2` | An ordinary requirement, looked up on PyPI. |

Like pip, a bare name is never a path: `pip-size proj` asks PyPI for a package called `proj` even
if a directory `proj` exists. Write `./proj` for the directory. A path that does not exist, or a
directory that is not a project, is an error that says so.

## What is built

| Source | What goes into the wheel |
| --- | --- |
| A directory | The **working tree as it is now**, including uncommitted changes. |
| A VCS URL | What is committed at that URL and ref (`@branch`, `@tag`, `@commit`). Local uncommitted work is not included. |
| An archive | The archive's contents. |

## How it is built

`pip-size` builds with `python -m pip wheel --no-deps` in the same Python that runs it. If pip is
not installed there, it falls back to `uv build --wheel` when `uv` is on the `PATH`, which works
for directories and archives but not for URLs.

!!! note "pipx and uvx"
    `pipx` environments contain pip, so everything works. `uvx` environments do not: directories
    and archives are built with `uv`, but a VCS URL needs pip and is refused with a message that
    says so. You can always build the wheel yourself (`uv build`, `python -m build --wheel`) and
    pass the `.whl`.

Directories are built from a **temporary copy**, so your tree is not modified. The copy leaves out
`.venv`, `venv`, `node_modules`, `build`, `dist`, `*.egg-info`, `__pycache__` and tool caches, and
keeps `.git`, so versions derived from git tags still work. The copy is deleted when the run ends.

!!! warning "Building runs code"
    Building a wheel executes the project's build backend, which can run arbitrary code, exactly
    as `pip install` and `pip wheel` do. Only build sources you trust. Build requirements (for
    example `hatchling`) are downloaded by pip for the duration of the build, and they are **not**
    counted in the size.

## Good to know

- **The size is that of a wheel built on your machine.** For pure-Python projects that is what users download. For projects with compiled extensions it is the wheel for your platform.
- **Nothing is cached.** Every run builds again, which can take seconds to minutes.
- **A build has a time limit** of 15 minutes.
- **Failures** (a broken build, no builder available) stop the run with exit status `1`; the last lines of the builder's output are shown.
- **Dependencies of the built package** are looked up on PyPI by name. A dependency that exists only as a URL is not built; see [Limitations](../concepts/limitations.md).

## From Python

```python
res = pip_size.resolve("./my-project")
res.root.built_from        # './my-project'
res.root.local             # True
```

Build problems raise [`BuildError`](../python-api/errors.md).
