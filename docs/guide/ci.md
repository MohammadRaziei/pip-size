# Use in CI

`pip-size` is built to be scripted: `--quiet --bytes` prints one integer, `--json` prints only
JSON, and the exit status tells you whether the package could be resolved.

## Fail a build when an install gets too big

```yaml title=".github/workflows/size.yml"
name: Install size

on: [pull_request]

jobs:
  size:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build pip-size
      - run: python -m build --wheel
      - name: Check the install size
        run: |
          size=$(pip-size dist/*.whl --quiet --bytes --skip-installed)
          echo "install size: $size bytes"
          test "$size" -lt 5000000
```

- `dist/*.whl` must match **one** wheel. Two wheels are two arguments and `pip-size` exits with status `2`.
- `--skip-installed` leaves out what the runner already has. Drop it to count everything a fresh machine would download.
- If `pip-size` fails, `$size` is empty and `test` fails the step, so a broken resolution cannot pass silently.

## Keep a size report

```yaml
      - name: Size report
        run: pip-size dist/*.whl --json > size.json
      - uses: actions/upload-artifact@v4
        with:
          name: size-report
          path: size.json
```

## Size against what your image already has

```yaml
      - name: Freeze the base image
        run: docker run --rm my-base-image pip freeze > base-freeze.txt
      - name: Size the new dependency
        run: pip-size mypkg --skip-file base-freeze.txt --quiet --bytes
```

## Tips

- **Results depend on the runner**: the wheel for the runner's Python and platform is chosen. Use the same Python version as production. See [Limitations](../concepts/limitations.md).
- **Network**: `pip-size` needs `pypi.org`. Use [`--proxy`](network.md) behind a corporate proxy.
- **Exit status**: `0` resolved, `1` could not resolve or read an input, `2` bad command line. See [Command line](../cli.md#exit-status).
