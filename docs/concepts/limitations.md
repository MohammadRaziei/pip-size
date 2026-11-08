# Limitations

`pip-size` estimates download size quickly and without installing anything. These are the things
to keep in mind when you rely on a number.

## What the number is

- **Compressed download size.** The sizes are those of the distribution files on PyPI, not the size installed on disk.
- **For the machine that runs `pip-size`.** The wheel for your Python version and platform is chosen, and markers are evaluated for your interpreter. `charset_normalizer`, for example, has a different wheel for each Python version. To size for another target, run `pip-size` with that Python, or use the same image as production.
- **With `--installed-from` the same holds.** Only the *skip list* comes from the other interpreter; wheel and marker selection still use the running one.

## Resolution

- **Not a backtracking resolver.** Each package is resolved once, against the first requirement that mentions it. If another requirement in the tree rejects the selected version, a **conflict** is reported rather than solved, so the version pip would finally install, and therefore its size, can differ.
- **Shared packages appear once**, under the first parent that needs them.
- **Extras of an already-resolved dependency are not merged in.** If a package was reached without an extra and is later required with it, the extra's dependencies are not added.
- **Source distributions.** If a package has no usable wheel, the size of its source distribution is used. Installing it would also build a wheel and may download build requirements, which are not counted.

## Inputs that are not supported

- **Only `pypi.org`.** There is no private index, `--index-url` or authentication.
- **Direct URL and VCS requirements among the dependencies** (`pkg @ https://...`) are looked up by name on PyPI. Only the package you ask about can be built from a URL.
- **Constraints files, `--pre` and platform overrides** (`--platform`, `--python-version`) do not exist.
- **Building needs a builder.** A directory, git URL or archive is built with pip (or, for directories and archives, `uv`). See [Directories, git URLs and archives](../guide/source-builds.md).

## Operations

- **Network is required** unless every response is cached, and the cache lasts 24 hours.
- **No retries.** A transient failure on the root is an error; on a dependency it becomes a warning and that package is missing from the total.
