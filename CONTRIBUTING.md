# Contributing to pip-size

Thanks for helping out. Bug reports and pull requests are welcome.

## Setup

```bash
git clone https://github.com/MohammadRaziei/pip-size
cd pip-size
pip install -e . pytest
```

## Running the tests

```bash
pytest                              # everything
pytest tests/test_resolve.py -k skip   # a subset
```

The tests run **offline**. `tests/conftest.py` replaces the network layer of `PyPIClient` with a
small in-memory fake of PyPI (the `fake_pypi` fixture) and points the cache at a temporary
directory, so nothing touches the network or your real cache. To test a new resolver behaviour,
add the packages it needs to `FAKE_PYPI` in `conftest.py`.

CI runs the suite on Python 3.9 to 3.13, builds the sdist and wheel, and smoke-tests the built
wheel against the real PyPI. Python 3.9 is the oldest supported version and differs at runtime in
places the other versions do not (for example, an `asyncio.Semaphore` binds to the current event
loop when it is created), so run the tests on 3.9 as well when you touch async code.

### CI layout

`.github/workflows/ci.yml` is the only workflow that reacts to repository events (pushes to
`master`, `v*` tags, pull requests). It does no work itself and calls three other workflows:

| Workflow | Does |
| --- | --- |
| `tests.yml` | Tests on Python 3.9 to 3.13, builds the sdist and wheel, smoke-tests the built wheel. |
| `docs.yml` | Builds the documentation (`mkdocs build --strict`); on `master` it also deploys to GitHub Pages. |
| `release.yml` | Creates the GitHub release and uploads to PyPI. Only for `v*` tags, after tests and docs pass. |

Each of the three can also be run alone from the Actions tab ("Run workflow"). `docs.yml` then
deploys unless you untick *deploy*; `release.yml` must be run from a `v*` tag (choose it under
*Use workflow from*) and builds the distributions itself.

With [Hatch](https://hatch.pypa.io): `hatch run test`, or `hatch run all:test` for the whole
version matrix.

## Code style

- Keep the code type-checkable: the package ships `py.typed`, and `mypy pip_size` is clean.
- `ruff` and `black` are configured in `pyproject.toml` (line length 120), but they are not
  enforced in CI yet and the existing code does not fully conform. Please do not mix a
  repository-wide reformat into a feature or fix.
- Python 3.9 compatibility: use `from __future__ import annotations` for `X | None` annotations,
  and no `match` statements.

## Project layout

| Module | Responsibility |
| --- | --- |
| `api.py` | Public `resolve()` / `resolve_async()` and the CLI-style `PipSize` class. |
| `__main__.py` | The `pip-size` command line. |
| `resolver.py` | Breadth-first dependency resolution: markers, extras, skip rules, conflicts. |
| `client.py` | Async PyPI client (Simple API and per-release JSON), proxies. |
| `selector.py` | Best file for the platform, best version for a specifier. |
| `skip.py` | Skip rules: names, globs, installed packages, freeze files. |
| `wheel.py` | Reading size and `METADATA` from a local `.whl`. |
| `build.py` | Building a wheel from a directory, VCS URL or archive (pip, or uv). |
| `models.py` | Result types: `PackageInfo`, `Conflict`, `Resolution`. |
| `cache.py` | On-disk response cache. |
| `printer.py` | Tree, JSON and plain output. |
| `errors.py` | The `PipSizeError` hierarchy. |
| `docs/`, `mkdocs.yml` | The documentation site, and the logo in `docs/assets/`. |

## Documentation

The documentation is a [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) site: the
pages are Markdown files in `docs/`, the navigation is in `mkdocs.yml`, and
`CHANGELOG.md` and `CONTRIBUTING.md` are included as pages.

```bash
pip install mkdocs-material
mkdocs serve              # live preview at http://127.0.0.1:8000
mkdocs build --strict     # what CI runs: broken links fail the build
```

With Hatch: `hatch run docs:serve` and `hatch run docs:build`. Pushes to `master` that touch the
docs are published to GitHub Pages by the CI workflow (`.github/workflows/docs.yml`).

## Adding an option

1. Implement it in the resolver (or the module it belongs to) with a test that uses `fake_pypi`.
2. Expose it as a keyword argument of `resolve_async()` in `api.py`, and of `PipSize`.
3. Add the command-line flag in `__main__.py` and a CLI test in `tests/test_cli.py`.
4. Document it in the docs (`docs/`) and, if it is user-facing, in the README. Example output must come from a real run.

## Releasing

1. Bump the version in `pip_size/__about__.py` and add an entry to `CHANGELOG.md`.
2. Push a tag named `vX.Y.Z`. The CI workflow (`.github/workflows/ci.yml`) runs the tests, builds
   the distributions, creates the GitHub release and uploads to PyPI.
