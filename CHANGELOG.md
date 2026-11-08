# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions below 1.0 may change the
public API between minor releases.

## [Unreleased]

### Added

- A documentation site built with MkDocs Material (`docs/`, `mkdocs.yml`): getting started, a user guide, the Python API reference, how it works and limitations. A workflow publishes it to GitHub Pages.

### Changed

- The README is shorter and links to the documentation site.
- The logo moved from `assets/` to `docs/assets/`.
- The `--extras` and `--proxy` help texts were rewritten: their examples used to run together in `--help` output.

## [0.3.0] - 2026-11-08

### Added

- **Local wheels.** `pip-size ./pkg-1.0-py3-none-any.whl` (and `...whl[extra]`) reads the size and `METADATA` from the file and resolves its dependencies from PyPI.
- **Skipping dependencies.** `--skip`, `--skip-installed`, `--installed-from` and `--skip-file` keep packages in the tree with their size, tagged `[skipped: ...]`, but leave them out of the total.
- **Python API.** `pip_size.resolve()` and `pip_size.resolve_async()` return a `Resolution` (`PackageInfo` tree, `conflicts`, `warnings`) and print nothing. Also `SkipRules`, `read_wheel` / `LocalWheel`, and the `PipSizeError` hierarchy. The package ships `py.typed`. Reference: `docs/python-api.md`.
- `--all-extras-deep`: every extra of every dependency at every level (the previous behaviour of `-a`).
- Version conflicts between requirements in the tree are reported instead of ignored.
- `--version`.
- A 30-second timeout on PyPI requests.

### Changed

- **Breaking:** requires Python 3.9 or newer. 0.2.0 declared 3.7, but the code already used APIs that need 3.9.
- **Breaking:** `-a` / `--all-extras` now activates every extra of the **root package** only, like pip does for `pkg[extra]`. Extras of dependencies are activated only when their parent asks for them. Use `--all-extras-deep` for the old behaviour; on large trees it can resolve thousands of packages.
- **Breaking (library):** the `pip_size.core` module is gone, split into `models`, `cache`, `client`, `selector`, `resolver` and `printer`. Import from the top-level `pip_size` package instead. `PipSize.run()` now returns `True` or `False`, and `Printer` methods take a `Resolution`.
- Versions and sizes now come from the PyPI Simple API (PEP 691) instead of the deprecated `releases` key of the JSON API. Yanked files are ignored, and pre-releases are used only when requested or unavoidable. A version is chosen only if it has a file usable on the running interpreter and platform.
- Dependencies are listed sorted by name; the order used to depend on network timing.
- `--json` output gains `size_bytes` / `total_size_bytes` on every node, `skipped`, `local`, and `conflicts` / `warnings` where they apply.
- The cache is written atomically, and its file names changed, so existing cache entries are ignored.
- CI runs the tests on Python 3.9 to 3.13 and releases only for `v*` tags (it used to publish a `__beta__` pre-release on every push to `master`).

### Fixed

- `--json` printed the header and progress lines on stdout, so the output was not valid JSON.
- An unresolvable package exited with status 0; it now exits with 1.
- The CI workflow's concurrency group had invalid syntax.

## [0.2.0]

See the [release page](https://github.com/MohammadRaziei/pip-size/releases/tag/v0.2.0).

[Unreleased]: https://github.com/MohammadRaziei/pip-size/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MohammadRaziei/pip-size/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MohammadRaziei/pip-size/releases/tag/v0.2.0
