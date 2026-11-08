"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    pip-size "requests"
    pip-size ./dist/mypkg-1.0-py3-none-any.whl          # local wheel: size + METADATA read from the file
    pip-size "requests" --no-deps
    pip-size "requests==2.31.0"
    pip-size "requests" --all-extras
    pip-size "requests" --verbose
    pip-size "requests" --extra-verbose
    pip-size "requests" --no-cache
    pip-size --clear-cache

Skipping (kept in the tree, shown with their size, not counted in the total):
    pip-size "fastapi" --skip numpy,pandas --skip "nvidia-*"
    pip-size "fastapi" --skip-installed
    pip freeze | pip-size "fastapi" --skip-file -

Proxy support:
    pip-size "requests" --proxy http://user:pass@host:8080
    pip-size "requests" --proxy socks5://user:pass@host:1080  # requires: pip install aiohttp-socks
    HTTP_PROXY=http://host:8080  pip-size "requests"          # env var fallback
    ALL_PROXY=socks5://host:1080 pip-size "requests"
"""

import sys
import logging
import argparse

from . import Cache, PipSize
from .__about__ import __version__

log = logging.getLogger("pip_size")

# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate real download size of a PyPI package. Zero downloads."
    )
    parser.add_argument(
        "package",
        nargs="?",
        help='a requirement ("requests", "requests==2.31.0", "requests[security]") '
             'or the path to a local .whl file (optionally "x.whl[extra]")',
    )
    parser.add_argument("--version", action="version", version=f"pip-size {__version__}")
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Show size of the package itself only, without resolving dependencies.",
    )
    parser.add_argument(
        "-a", "--all-extras",
        action="store_true",
        help=(
            "Activate EVERY extra declared by the root package (worst-case install of this "
            "package). Extras of dependencies are activated only when their parent requests "
            "them, like pip. Each extra-gated package is labelled [extra: <name>]."
        ),
    )
    parser.add_argument(
        "--all-extras-deep",
        action="store_true",
        help=(
            "Like --all-extras, but also activate every extra of every dependency at every "
            "level. Unbounded: test/dev extras are included, so the tree can reach thousands "
            "of packages."
        ),
    )
    parser.add_argument(
        "--extras",
        metavar="PATTERNS",
        default=None,
        help=(
            "Comma-separated names or glob patterns of extras to activate on the ROOT package "
            "only, like pkg[extra1,extra2] but with wildcards. Examples: 'test', 'test,dev', "
            "'dev*', '*' (every extra). Can be combined with --all-extras."
        ),
    )
    skipping = parser.add_argument_group(
        "skipping",
        "Skipped dependencies stay in the tree with their size and are tagged [skipped: ...], "
        "but are not added to the total and their own dependencies are not expanded.",
    )
    skipping.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="NAMES",
        help="Comma-separated package names or glob patterns to skip (repeatable), e.g. numpy,'nvidia-*'.",
    )
    skipping.add_argument(
        "--skip-installed",
        action="store_true",
        help="Skip packages already installed in this environment, "
             "when the installed version satisfies the requirement.",
    )
    skipping.add_argument(
        "--installed-from",
        metavar="PYTHON",
        default=None,
        help="Like --skip-installed, but inspect the environment of this python executable "
             "(e.g. /opt/venv/bin/python). Implies --skip-installed.",
    )
    skipping.add_argument(
        "--skip-file",
        action="append",
        default=[],
        metavar="FILE",
        help="Skip every package listed in a pip-freeze / requirements file (repeatable). "
             "Use '-' to read the list from stdin.",
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        default=None,
        help=(
            "Proxy URL for all PyPI requests; overrides the HTTPS_PROXY / HTTP_PROXY / ALL_PROXY "
            "environment variables. Examples: http://user:pass@host:8080, socks5://host:1080 "
            "(SOCKS needs: pip install 'pip-size[socks]')."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache and always fetch fresh data from PyPI.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=f"Delete all cached PyPI responses and exit.  (cache dir: {Cache.directory()})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the total size and nothing else.",
    )
    parser.add_argument(
        "--bytes",
        action="store_true",
        help="Report all sizes in raw bytes instead of human-readable units.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the full dependency tree as JSON.",
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help="Enable INFO logging.",
    )
    verbosity.add_argument(
        "--extra-verbose",
        action="store_true",
        help="Enable DEBUG logging (HTTP, wheel scoring, marker evaluation, cache).",
    )

    args = parser.parse_args()

    if args.extra_verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)-8s  %(name)s  %(message)s")
    elif args.verbose:
        logging.basicConfig(level=logging.INFO,  format="%(levelname)-8s  %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.clear_cache:
        removed = Cache.clear()
        print(f"🗑  Cache cleared — {removed} file(s) removed.  ({Cache.directory()})")
        sys.exit(0)

    if not args.package:
        parser.error("the following arguments are required: package")

    try:
        extras_filter = None
        if args.extras:
            extras_filter = {p.strip() for p in args.extras.split(",") if p.strip()}

        ok = PipSize(
            no_deps       = args.no_deps,
            all_extras    = args.all_extras,
            all_extras_deep = args.all_extras_deep,
            extras_filter = extras_filter,
            use_cache     = not args.no_cache,
            proxy         = args.proxy,
            quiet         = args.quiet,
            as_bytes      = args.bytes,
            as_json       = args.json,
            skip          = args.skip,
            skip_installed= args.skip_installed,
            skip_files    = args.skip_file,
            installed_from= args.installed_from,
        ).run(args.package)
        if not ok:
            sys.exit(1)
    except RuntimeError as exc:
        print(f"\n❌ Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()