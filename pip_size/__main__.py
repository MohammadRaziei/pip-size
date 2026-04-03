"""
pip-size: Calculate the real download size of a PyPI package and its dependencies.
No downloads. No pip subprocess. Pure PyPI JSON API + packaging.

Usage:
    pip-size "requests"
    pip-size "requests" --no-deps
    pip-size "requests==2.31.0"
    pip-size "requests" --include-optional
    pip-size "requests" --verbose
    pip-size "requests" --extra-verbose
    pip-size "requests" --no-cache
    pip-size --clear-cache

Proxy support:
    pip-size "requests" --proxy http://user:pass@host:8080
    pip-size "requests" --proxy socks5://user:pass@host:1080  # requires: pip install aiohttp-socks
    HTTP_PROXY=http://host:8080  pip-size "requests"          # env var fallback
    ALL_PROXY=socks5://host:1080 pip-size "requests"
"""

import sys
import logging
import argparse

from . import PipSize, Cache

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
        help='e.g. "requests" or "requests==2.31.0"',
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Show size of the package itself only, without resolving dependencies.",
    )
    parser.add_argument(
        "-a", "--all-extras",
        action="store_true",
        help=(
            "Include ALL optional dependencies (extra-gated) across the entire "
            "dependency tree. Each one is labelled [extra: <name>] in the output."
        ),
    )
    parser.add_argument(
        "--extras",
        metavar="PATTERNS",
        default=None,
        help=(
            "Comma-separated glob patterns selecting which extras to activate for "
            "the ROOT package only (same as writing pkg[extra1,extra2] on the CLI, "
            "but supports wildcards).\n"
            "  --extras test          # exactly 'test'\n"
            "  --extras 'test,dev'    # 'test' and 'dev'\n"
            "  --extras 'dev*'        # anything starting with 'dev'\n"
            "  --extras '*'           # every extra on the root package\n"
            "Can be combined with --all-extras."
        ),
    )
    parser.add_argument(
        "--proxy",
        metavar="URL",
        default=None,
        help=(
            "Proxy URL for all PyPI requests (overrides HTTP_PROXY / ALL_PROXY env vars).\n"
            "  HTTP:   --proxy http://user:pass@host:8080\n"
            "  SOCKS5: --proxy socks5://user:pass@host:1080  (requires: pip install aiohttp-socks)\n"
            "  SOCKS4: --proxy socks4://host:1080            (requires: pip install aiohttp-socks)"
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
        extras_filter: set[str] | None = None
        if args.extras:
            extras_filter = {p.strip() for p in args.extras.split(",") if p.strip()}

        PipSize(
            no_deps       = args.no_deps,
            all_extras    = args.all_extras,
            extras_filter = extras_filter,
            use_cache     = not args.no_cache,
            proxy         = args.proxy,
            quiet         = args.quiet,
            as_bytes      = args.bytes,
            as_json       = args.json,
        ).run(args.package)
    except RuntimeError as exc:
        log.error("%s", exc)
        print(f"\n❌ Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()