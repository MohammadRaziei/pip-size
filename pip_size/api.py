"""
Public API.

    import pip_size

    res = pip_size.resolve("requests")                       # sync
    res = await pip_size.resolve_async("requests")           # async
    res = pip_size.resolve("./dist/mypkg-1.0-py3-none-any.whl")

    res.total_size            # bytes to download (skipped packages not counted)
    res.skipped_size          # bytes of the skipped packages
    for pkg in res.packages:  # whole tree, root first
        print(pkg.name, pkg.version, pkg.size, pkg.skipped)

    # Skip what is already there. Skipped packages stay in the tree, marked, with their size.
    res = pip_size.resolve("fastapi", skip=["numpy", "nvidia-*"])
    res = pip_size.resolve("fastapi", skip_installed=True)
    res = pip_size.resolve("fastapi", skip_files=["frozen.txt"])         # pip freeze output
    res = pip_size.resolve("fastapi", skip_installed=True, installed_from="/opt/venv/bin/python")
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Callable, Iterable

from packaging.requirements import InvalidRequirement, Requirement

from .cache import Cache
from .client import PyPIClient, resolve_proxy_url
from .models import Resolution
from .printer import Printer
from .resolver import DependencyResolver
from .errors import PipSizeError, ResolutionError
from .skip import SkipRules
from .wheel import parse_wheel_spec, read_wheel

log = logging.getLogger(__name__)


def _as_set(values: str | Iterable[str] | None) -> set[str] | None:
    if not values:
        return None
    items = [values] if isinstance(values, str) else list(values)
    out = {tok.strip() for item in items for tok in item.split(",") if tok.strip()}
    return out or None


async def resolve_async(
    spec:           str | os.PathLike[str] | Requirement,
    *,
    no_deps:        bool                         = False,
    all_extras:     bool                         = False,
    all_extras_deep: bool                        = False,
    extras:         str | Iterable[str] | None   = None,
    skip:           str | Iterable[str]          = (),
    skip_installed: bool                         = False,
    skip_files:     Iterable[str]                = (),
    installed_from: str | None                   = None,
    skip_rules:     SkipRules | None             = None,
    use_cache:      bool                         = True,
    proxy:          str | None                   = None,
    progress:       Callable[[str], None] | None = None,
) -> Resolution:
    """
    Resolve *spec* and return a Resolution. Nothing is printed unless *progress* is given.

    spec            a requirement ("requests", "requests==2.31.0", "requests[security]"),
                    a Requirement, or the path to a local .whl (optionally "x.whl[extra]")
    no_deps         only the package itself
    all_extras      activate every extra declared by the ROOT package
    all_extras_deep also activate every extra of every dependency (unbounded: can resolve thousands of packages)
    extras          glob patterns for extras of the ROOT package ("test", "dev*", "*")
    skip            names / glob patterns to keep in the tree but not count
    skip_installed  also skip packages installed in this environment (version must satisfy the requirement)
    skip_files      pip-freeze / requirements files whose packages are skipped ("-" = stdin)
    installed_from  python executable whose environment --skip_installed inspects
    skip_rules      a prebuilt SkipRules (overrides the four skip arguments above)

    Raises ResolutionError if the root package cannot be resolved.
    """
    rules = skip_rules or SkipRules.build(
        names=skip, skip_installed=skip_installed, freeze_files=skip_files, python=installed_from,
    )
    extras_filter = _as_set(extras)

    wheel = None
    req   = None
    wheel_extras: set[str] = set()
    if isinstance(spec, Requirement):
        req = spec
    else:
        text   = os.fspath(spec)
        parsed = parse_wheel_spec(text)
        if parsed is not None:
            path, wheel_extras = parsed
            wheel = read_wheel(path)                # raises WheelError (a PipSizeError)
        else:
            try:
                req = Requirement(text)
            except InvalidRequirement as exc:
                raise ResolutionError(f"Invalid requirement {text!r}: {exc}") from exc

    async with PyPIClient(proxy=proxy, use_cache=use_cache) as client:
        resolver = DependencyResolver(
            client        = client,
            all_extras    = all_extras,
            all_extras_deep = all_extras_deep,
            extras_filter = extras_filter,
            no_deps       = no_deps,
            skip          = rules,
            progress      = progress,
        )
        if wheel is not None:
            root = await resolver.resolve_wheel(wheel, wheel_extras)
        elif req is not None:
            root = await resolver.resolve(req)
        else:  # pragma: no cover - the branches above always set one of them
            raise ResolutionError("no package given")

    return Resolution(root=root, conflicts=resolver.conflicts, warnings=resolver.warnings)


def _require_no_running_loop(name: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        f"{name}() cannot be called from a running event loop (async code, Jupyter, ...). "
        "Use `await pip_size.resolve_async(...)` there instead."
    )


def resolve(spec: str | os.PathLike[str] | Requirement, **kwargs) -> Resolution:
    """
    Synchronous wrapper around :func:`resolve_async` (same arguments).

    Not usable from inside a running event loop (async code, Jupyter): call
    ``await resolve_async(...)`` there.
    """
    _require_no_running_loop("resolve")
    return asyncio.run(resolve_async(spec, **kwargs))


class PipSize:
    """
    The command-line behaviour as a class: resolve, then print a tree / JSON / a bare number.

    Sync usage:
        PipSize(no_deps=True).run("requests==2.31.0")

    Async usage:
        await PipSize().run_async("requests")

    For programmatic use prefer :func:`resolve`, which returns data instead of printing.
    """

    def __init__(
        self,
        no_deps:        bool                = False,
        all_extras:     bool                = False,
        all_extras_deep: bool               = False,
        extras_filter:  set[str] | None     = None,
        use_cache:      bool                = True,
        proxy:          str | None          = None,
        quiet:          bool                = False,
        as_bytes:       bool                = False,
        as_json:        bool                = False,
        skip:           str | Iterable[str] = (),
        skip_installed: bool                = False,
        skip_files:     Iterable[str]       = (),
        installed_from: str | None          = None,
    ) -> None:
        self.no_deps        = no_deps
        self.all_extras     = all_extras
        self.all_extras_deep = all_extras_deep
        self.extras_filter  = extras_filter
        self.use_cache      = use_cache
        self.proxy          = proxy
        self.quiet          = quiet
        self.as_bytes       = as_bytes
        self.as_json        = as_json
        self.skip           = skip
        self.skip_installed = skip_installed
        self.skip_files     = list(skip_files)
        self.installed_from = installed_from

    def run(self, package_spec: str) -> bool:
        """Run synchronously. Returns True if the package was resolved."""
        _require_no_running_loop("PipSize.run")
        return asyncio.run(self.run_async(package_spec))

    async def run_async(self, package_spec: str) -> bool:
        machine = self.quiet or self.as_json          # stdout must stay clean
        printer = Printer(as_bytes=self.as_bytes)

        self._print_header(package_spec)
        log.info(
            "starting  spec=%s  no_deps=%s  all_extras=%s  extras_filter=%s  cache=%s  proxy=%s",
            package_spec, self.no_deps, self.all_extras, self.extras_filter,
            self.use_cache, resolve_proxy_url(self.proxy) or "none",
        )
        log.debug("cache dir: %s", Cache.directory())

        try:
            res = await resolve_async(
                package_spec,
                no_deps        = self.no_deps,
                all_extras     = self.all_extras,
                all_extras_deep = self.all_extras_deep,
                extras         = self.extras_filter,
                skip           = self.skip,
                skip_installed = self.skip_installed,
                skip_files     = self.skip_files,
                installed_from = self.installed_from,
                use_cache      = self.use_cache,
                proxy          = self.proxy,
                progress       = None if machine else print,
            )
        except PipSizeError as exc:
            if not machine:
                print(f"\n❌ {exc}", file=sys.stderr)
            return False

        log.info("resolved  total=%s", Printer.format_size(res.total_size))

        if self.as_json:
            printer.print_json(res)
        elif self.quiet:
            printer.print_quiet(res)
        else:
            printer.print_tree(res)
        return True

    def _print_header(self, package_spec: str) -> None:
        if self.quiet or self.as_json:
            return
        notes = []
        if not self.use_cache:
            notes.append("cache disabled")
        if self.all_extras_deep:
            notes.append("all extras, deep")
        elif self.all_extras:
            notes.append("all extras")
        if self.extras_filter:
            notes.append(f"extras: {', '.join(sorted(self.extras_filter))}")
        if self.skip:
            names = [self.skip] if isinstance(self.skip, str) else list(self.skip)
            notes.append(f"skip: {', '.join(names)}")
        if self.skip_installed or self.installed_from:
            notes.append("skipping installed packages")
        if self.skip_files:
            notes.append("skip files: " + ", ".join(self.skip_files))
        proxy = resolve_proxy_url(self.proxy)
        if proxy:
            notes.append(f"proxy: {proxy}")
        suffix = f"  ({', '.join(notes)})" if notes else ""
        print(f"\n🔍 Resolving '{package_spec}'...{suffix}")
