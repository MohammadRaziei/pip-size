"""
Unit tests for pip_size core classes.
Framework : unittest.TestCase
Runner    : pytest test_core.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from packaging.requirements import Requirement

from pip_size.core import (
    Cache,
    DependencyResolver,
    PackageInfo,
    Printer,
    WheelSelector,
    _SKIP,
)


# ─────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────

def make_pkg(name="requests", version="2.31.0", size=1000, deps=None, via_extra=None):
    return PackageInfo(
        name=name,
        version=version,
        size=size,
        filename=f"{name}-{version}-py3-none-any.whl",
        via_extra=via_extra,
        dependencies=deps or [],
    )


def make_file(filename: str, size: int, requires_python: str | None = None) -> dict:
    f: dict = {"filename": filename, "size": size}
    if requires_python:
        f["requires_python"] = requires_python
    return f


def pypi_response(
    name: str,
    version: str,
    requires_dist: list[str] | None = None,
    extra_releases: dict | None = None,
) -> dict:
    releases: dict = {
        version: [make_file(f"{name}-{version}-py3-none-any.whl", 1000)]
    }
    if extra_releases:
        releases.update(extra_releases)
    return {
        "info": {
            "name": name,
            "version": version,
            "requires_dist": requires_dist or [],
        },
        "releases": releases,
    }


class FakeClient:
    proxy_url = None
    use_cache = False

    def __init__(self, responses: dict[str, dict]):
        self._responses = {k.lower(): v for k, v in responses.items()}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def fetch(self, package_name: str) -> dict:
        key = package_name.lower()
        if key not in self._responses:
            raise RuntimeError(f"HTTP 404: Not Found  ({package_name})")
        return self._responses[key]


def resolve(req_str: str, responses: dict, **kwargs) -> PackageInfo | None:
    client   = FakeClient(responses)
    resolver = DependencyResolver(client=client, **kwargs)
    return asyncio.get_event_loop().run_until_complete(
        resolver.resolve(Requirement(req_str))
    )


def make_resolver(all_extras=False) -> DependencyResolver:
    return DependencyResolver(client=MagicMock(), all_extras=all_extras)


# ─────────────────────────────────────────────────────────────────────
# PackageInfo
# ─────────────────────────────────────────────────────────────────────

class TestPackageInfo(unittest.TestCase):

    def test_total_size_leaf(self):
        self.assertEqual(make_pkg(size=500).total_size(), 500)

    def test_total_size_with_direct_deps(self):
        root = make_pkg(size=100, deps=[
            make_pkg("urllib3", size=300),
            make_pkg("certifi", size=200),
        ])
        self.assertEqual(root.total_size(), 600)

    def test_total_size_nested(self):
        grandchild = make_pkg("h11",      size=50)
        child      = make_pkg("httpcore", size=100, deps=[grandchild])
        root       = make_pkg("httpx",    size=200, deps=[child])
        self.assertEqual(root.total_size(), 350)

    def test_via_extra_default_none(self):
        self.assertIsNone(make_pkg().via_extra)

    def test_dependencies_default_empty(self):
        self.assertEqual(make_pkg().dependencies, [])


# ─────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────

class TestCache(unittest.TestCase):
    URL  = "https://pypi.org/pypi/requests/json"
    DATA = {"info": {"name": "requests", "version": "2.31.0"}}

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._patcher = patch.object(Cache, "directory", return_value=__import__("pathlib").Path(self._tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_miss_on_empty_dir(self):
        self.assertIsNone(Cache.read(self.URL))

    def test_write_then_read(self):
        Cache.write(self.URL, self.DATA)
        self.assertEqual(Cache.read(self.URL), self.DATA)

    def test_expired_entry_returns_none_and_is_deleted(self):
        Cache.write(self.URL, self.DATA)
        path      = Cache._path(self.URL)
        old_mtime = time.time() - Cache.TTL - 1
        os.utime(path, (old_mtime, old_mtime))
        self.assertIsNone(Cache.read(self.URL))
        self.assertFalse(path.exists())

    def test_corrupted_file_returns_none_and_is_deleted(self):
        path = Cache._path(self.URL)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json", encoding="utf-8")
        self.assertIsNone(Cache.read(self.URL))
        self.assertFalse(path.exists())

    def test_clear_removes_all_files(self):
        for name in ("requests", "urllib3", "certifi"):
            Cache.write(f"https://pypi.org/pypi/{name}/json", {"x": name})
        self.assertEqual(Cache.clear(), 3)

    def test_clear_on_nonexistent_dir_returns_zero(self):
        import pathlib, tempfile
        ghost = pathlib.Path(tempfile.mkdtemp()) / "ghost"
        with patch.object(Cache, "directory", return_value=ghost):
            self.assertEqual(Cache.clear(), 0)

    def test_path_has_no_slashes_in_name(self):
        path = Cache._path(self.URL)
        self.assertNotIn("/", path.name)
        self.assertEqual(path.suffix, ".json")


# ─────────────────────────────────────────────────────────────────────
# WheelSelector
# ─────────────────────────────────────────────────────────────────────

class TestWheelSelector(unittest.TestCase):

    def test_empty_list_returns_none(self):
        self.assertIsNone(WheelSelector.best([]))

    def test_pure_python_wheel_returned(self):
        files  = [make_file("pkg-1.0-py3-none-any.whl", 1000)]
        result = WheelSelector.best(files)
        self.assertIsNotNone(result)
        self.assertEqual(result["filename"], "pkg-1.0-py3-none-any.whl")

    def test_wheel_preferred_over_sdist(self):
        files = [
            make_file("pkg-1.0.tar.gz",           9000),
            make_file("pkg-1.0-py3-none-any.whl", 1000),
        ]
        self.assertTrue(WheelSelector.best(files)["filename"].endswith(".whl"))

    def test_incompatible_python_version_skipped(self):
        files = [make_file("pkg-1.0-py3-none-any.whl", 500, requires_python="==2.7.*")]
        self.assertIsNone(WheelSelector.best(files))

    def test_malformed_requires_python_does_not_crash(self):
        files = [make_file("pkg-1.0-py3-none-any.whl", 500, requires_python=">=3.5.*")]
        try:
            WheelSelector.best(files)
        except Exception as exc:
            self.fail(f"WheelSelector.best raised unexpectedly: {exc}")

    def test_sdist_fallback_when_no_compatible_wheel(self):
        files = [
            make_file("pkg-1.0-cp00-cp00-manylinux_9_9_fakearch.whl", 800),
            make_file("pkg-1.0.tar.gz", 900),
        ]
        result = WheelSelector.best(files)
        self.assertIsNotNone(result)
        self.assertEqual(result["filename"], "pkg-1.0.tar.gz")

    def test_size_is_preserved(self):
        files  = [make_file("pkg-1.0-py3-none-any.whl", 12345)]
        result = WheelSelector.best(files)
        self.assertEqual(result["size"], 12345)


# ─────────────────────────────────────────────────────────────────────
# Printer
# ─────────────────────────────────────────────────────────────────────

class TestPrinterFormatSize(unittest.TestCase):

    def test_bytes(self):
        self.assertEqual(Printer.format_size(0),   "0.0 B")
        self.assertEqual(Printer.format_size(500), "500.0 B")

    def test_kilobytes(self):
        self.assertEqual(Printer.format_size(1024),       "1.0 KB")
        self.assertEqual(Printer.format_size(1024 * 512), "512.0 KB")

    def test_megabytes(self):
        self.assertEqual(Printer.format_size(1024 ** 2), "1.0 MB")

    def test_gigabytes(self):
        self.assertEqual(Printer.format_size(1024 ** 3), "1.0 GB")


class TestPrinterSizeStr(unittest.TestCase):

    def test_leaf_shows_only_own_size(self):
        s = Printer()._size_str(make_pkg(size=2048))
        self.assertEqual(s, "2.0 KB")
        self.assertNotIn("total", s)

    def test_node_with_deps_shows_own_and_total(self):
        child = make_pkg("urllib3",  size=1024)
        root  = make_pkg("requests", size=1024, deps=[child])
        s     = Printer()._size_str(root)
        self.assertIn("1.0 KB",  s)
        self.assertIn("total:",  s)
        self.assertIn("2.0 KB",  s)

    def test_as_bytes_mode(self):
        s = Printer(as_bytes=True)._size_str(make_pkg(size=1234))
        self.assertEqual(s, "1234")


class TestPrinterToDict(unittest.TestCase):

    def test_leaf_structure(self):
        d = Printer()._to_dict(make_pkg(name="urllib3", version="2.0.0", size=1024))
        self.assertEqual(d["name"],       "urllib3")
        self.assertEqual(d["version"],    "2.0.0")
        self.assertEqual(d["size"],       "1.0 KB")
        self.assertEqual(d["total_size"], "1.0 KB")
        self.assertNotIn("dependencies", d)

    def test_nested_structure(self):
        child = make_pkg("certifi", size=512)
        root  = make_pkg("requests", size=512, deps=[child])
        d     = Printer()._to_dict(root)
        self.assertEqual(len(d["dependencies"]), 1)
        self.assertEqual(d["dependencies"][0]["name"], "certifi")
        self.assertEqual(d["total_size"], "1.0 KB")

    def test_via_extra_included_when_set(self):
        d = Printer()._to_dict(make_pkg(via_extra="security"))
        self.assertEqual(d["via_extra"], "security")

    def test_via_extra_absent_when_none(self):
        self.assertNotIn("via_extra", Printer()._to_dict(make_pkg()))


class TestPrinterOutput(unittest.TestCase):

    def test_print_tree_contains_name_and_version(self):
        child = make_pkg("urllib3", size=300)
        root  = make_pkg("requests", size=100, deps=[child])
        with patch("builtins.print") as mock_print:
            Printer().print_tree(root)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("requests==2.31.0", output)
        self.assertIn("urllib3==2.31.0",  output)

    def test_print_tree_shows_total_for_root(self):
        child = make_pkg("urllib3", size=300)
        root  = make_pkg("requests", size=100, deps=[child])
        with patch("builtins.print") as mock_print:
            Printer().print_tree(root)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("total:", output)

    def test_print_tree_leaf_no_total(self):
        with patch("builtins.print") as mock_print:
            Printer().print_tree(make_pkg(size=1024))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertNotIn("total:", output)

    def test_print_quiet_only_size(self):
        with patch("builtins.print") as mock_print:
            Printer().print_quiet(make_pkg(size=1024))
        mock_print.assert_called_once_with("1.0 KB")

    def test_print_quiet_as_bytes(self):
        with patch("builtins.print") as mock_print:
            Printer(as_bytes=True).print_quiet(make_pkg(size=1024))
        mock_print.assert_called_once_with("1024")

    def test_print_json_valid(self):
        captured = []
        with patch("builtins.print", side_effect=captured.append):
            Printer().print_json(make_pkg())
        data = json.loads(captured[0])
        self.assertEqual(data["name"], "requests")

    def test_print_tree_extra_tag(self):
        child = make_pkg("cryptography", size=500, via_extra="security")
        root  = make_pkg("requests",     size=100, deps=[child])
        with patch("builtins.print") as mock_print:
            Printer().print_tree(root)
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("[extra: security]", output)

    def test_print_tree_connector_symbols(self):
        deps = [make_pkg(f"dep{i}", size=100) for i in range(3)]
        with patch("builtins.print") as mock_print:
            Printer().print_tree(make_pkg(size=100, deps=deps))
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("├──", output)
        self.assertIn("└──", output)


# ─────────────────────────────────────────────────────────────────────
# DependencyResolver._evaluate_marker
# ─────────────────────────────────────────────────────────────────────

class TestEvaluateMarker(unittest.TestCase):

    def test_no_marker_returns_none(self):
        r = make_resolver()
        self.assertIsNone(r._evaluate_marker(Requirement("urllib3"), set()))

    def test_non_extra_marker_satisfied_returns_none(self):
        r = make_resolver()
        self.assertIsNone(
            r._evaluate_marker(Requirement('urllib3; python_version >= "2.7"'), set())
        )

    def test_non_extra_marker_not_satisfied_returns_skip(self):
        r = make_resolver()
        self.assertIs(
            r._evaluate_marker(Requirement('urllib3; python_version == "1.0"'), set()),
            _SKIP,
        )

    def test_optional_no_active_extra_returns_skip(self):
        r = make_resolver(all_extras=False)
        self.assertIs(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), set()),
            _SKIP,
        )

    def test_optional_matching_active_extra_returns_name(self):
        r = make_resolver(all_extras=False)
        self.assertEqual(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), {"security"}),
            "security",
        )

    def test_optional_wrong_active_extra_returns_skip(self):
        r = make_resolver(all_extras=False)
        self.assertIs(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), {"test"}),
            _SKIP,
        )

    def test_glob_prefix_matches_extra(self):
        r = make_resolver(all_extras=False)
        self.assertEqual(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), {"sec*"}),
            "security",
        )

    def test_glob_star_matches_any_extra(self):
        r = make_resolver(all_extras=False)
        self.assertEqual(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), {"*"}),
            "security",
        )

    def test_all_extras_flag_includes_optional(self):
        r = make_resolver(all_extras=True)
        self.assertEqual(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), set()),
            "security",
        )

    def test_active_extras_do_not_bleed_to_children(self):
        r = make_resolver(all_extras=False)
        self.assertIs(
            r._evaluate_marker(Requirement('cryptography; extra == "security"'), set()),
            _SKIP,
        )

    def test_extract_extra_name(self):
        req = Requirement('cryptography; extra == "security"')
        self.assertEqual(DependencyResolver._extract_extra_name(req), "security")


# ─────────────────────────────────────────────────────────────────────
# DependencyResolver BFS
# ─────────────────────────────────────────────────────────────────────

class TestDependencyResolverBFS(unittest.TestCase):

    def test_simple_package_no_deps(self):
        pkg = resolve("requests", {"requests": pypi_response("requests", "2.31.0")})
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg.name,    "requests")
        self.assertEqual(pkg.version, "2.31.0")
        self.assertEqual(pkg.dependencies, [])

    def test_one_level_deps(self):
        pkg = resolve("requests", {
            "requests": pypi_response("requests", "2.31.0", requires_dist=["urllib3"]),
            "urllib3":  pypi_response("urllib3",  "2.0.0"),
        })
        self.assertEqual(len(pkg.dependencies), 1)
        self.assertEqual(pkg.dependencies[0].name, "urllib3")

    def test_no_deps_flag_skips_resolution(self):
        pkg = resolve("requests", {
            "requests": pypi_response("requests", "2.31.0", requires_dist=["urllib3"]),
            "urllib3":  pypi_response("urllib3",  "2.0.0"),
        }, no_deps=True)
        self.assertEqual(pkg.dependencies, [])

    def test_deduplication_diamond(self):
        """A -> B, A -> C, B -> D, C -> D  =>  D appears only once."""
        pkg = resolve("a", {
            "a": pypi_response("a", "1.0", requires_dist=["b", "c"]),
            "b": pypi_response("b", "1.0", requires_dist=["d"]),
            "c": pypi_response("c", "1.0", requires_dist=["d"]),
            "d": pypi_response("d", "1.0"),
        })

        def all_names(p):
            result = {p.name}
            for dep in p.dependencies:
                result |= all_names(dep)
            return result

        self.assertEqual(all_names(pkg), {"a", "b", "c", "d"})

    def test_404_dep_does_not_crash(self):
        pkg = resolve("requests", {
            "requests": pypi_response("requests", "2.31.0", requires_dist=["urllib3"]),
        }, quiet=True)
        self.assertIsNotNone(pkg)

    def test_optional_dep_excluded_without_extra(self):
        pkg = resolve("requests", {
            "requests":     pypi_response("requests", "2.31.0",
                requires_dist=['cryptography; extra == "security"']),
            "cryptography": pypi_response("cryptography", "41.0.0"),
        })
        names = [d.name for d in pkg.dependencies]
        self.assertNotIn("cryptography", names)

    def test_optional_dep_included_with_bracket_extra(self):
        pkg = resolve("requests[security]", {
            "requests":     pypi_response("requests", "2.31.0",
                requires_dist=['cryptography; extra == "security"']),
            "cryptography": pypi_response("cryptography", "41.0.0"),
        })
        names = [d.name for d in pkg.dependencies]
        self.assertIn("cryptography", names)

    def test_optional_dep_included_with_extras_filter(self):
        pkg = resolve("requests", {
            "requests":     pypi_response("requests", "2.31.0",
                requires_dist=['cryptography; extra == "security"']),
            "cryptography": pypi_response("cryptography", "41.0.0"),
        }, extras_filter={"security"})
        names = [d.name for d in pkg.dependencies]
        self.assertIn("cryptography", names)

    def test_optional_dep_included_with_all_extras(self):
        pkg = resolve("requests", {
            "requests":     pypi_response("requests", "2.31.0",
                requires_dist=['cryptography; extra == "security"']),
            "cryptography": pypi_response("cryptography", "41.0.0"),
        }, all_extras=True)
        names = [d.name for d in pkg.dependencies]
        self.assertIn("cryptography", names)

    def test_optional_not_propagated_to_grandchild(self):
        """
        a[test] -> b (via extra=test)
        b has its own optional dep c (extra=test on b).
        c must NOT be included because b was not requested with [test].
        """
        pkg = resolve("a[test]", {
            "a": pypi_response("a", "1.0", requires_dist=['b; extra == "test"']),
            "b": pypi_response("b", "1.0", requires_dist=['c; extra == "test"']),
            "c": pypi_response("c", "1.0"),
        })
        b = next(d for d in pkg.dependencies if d.name == "b")
        self.assertEqual(b.dependencies, [])

    def test_extras_in_requires_dist_propagate_correctly(self):
        """
        a -> b[myextra]  (b declared WITH extra by a)
        b[myextra] unlocks c  =>  c must be included.
        """
        pkg = resolve("a", {
            "a": pypi_response("a", "1.0", requires_dist=["b[myextra]"]),
            "b": pypi_response("b", "1.0", requires_dist=['c; extra == "myextra"']),
            "c": pypi_response("c", "1.0"),
        })
        b = next(d for d in pkg.dependencies if d.name == "b")
        names = [d.name for d in b.dependencies]
        self.assertIn("c", names)

    def test_malformed_version_in_releases_skipped(self):
        response = pypi_response("requests", "2.31.0")
        response["releases"]["1.3.0.dev-r634"] = [
            make_file("requests-1.3.0.dev-r634.tar.gz", 500)
        ]
        pkg = resolve("requests", {"requests": response})
        self.assertIsNotNone(pkg)

    def test_size_taken_from_best_file(self):
        pkg = resolve("requests", {
            "requests": {
                "info": {"name": "requests", "version": "2.31.0", "requires_dist": []},
                "releases": {
                    "2.31.0": [make_file("requests-2.31.0-py3-none-any.whl", 99999)]
                },
            }
        })
        self.assertEqual(pkg.size, 99999)

    def test_via_extra_label_set_on_optional_dep(self):
        pkg = resolve("requests[security]", {
            "requests":     pypi_response("requests", "2.31.0",
                requires_dist=['cryptography; extra == "security"']),
            "cryptography": pypi_response("cryptography", "41.0.0"),
        })
        crypto = next(d for d in pkg.dependencies if d.name == "cryptography")
        self.assertEqual(crypto.via_extra, "security")


if __name__ == "__main__":
    unittest.main()