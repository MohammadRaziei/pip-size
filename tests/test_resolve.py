import json

import pytest
from packaging.specifiers import SpecifierSet

from pip_size import Cache, PipSize, Printer
from pip_size.client import _group_by_version
from pip_size.models import PackageInfo
from pip_size.selector import WheelSelector, pick_release

pytestmark = pytest.mark.usefixtures("fake_pypi")


def test_format_size():
    assert Printer.format_size(0) == "0.0 B"
    assert Printer.format_size(1536) == "1.5 KB"
    assert Printer.format_size(5 * 1024 ** 2) == "5.0 MB"


def test_quiet_prints_total_with_deps(capsys):
    assert PipSize(quiet=True, as_bytes=True).run("root") is True
    assert capsys.readouterr().out.strip() == str(1000 + 200 + 50)


def test_no_deps(capsys):
    PipSize(quiet=True, as_bytes=True, no_deps=True).run("root")
    assert capsys.readouterr().out.strip() == "1000"


def test_human_readable_by_default(capsys):
    PipSize(quiet=True, no_deps=True).run("root")
    assert capsys.readouterr().out.strip() == "1000.0 B"


def test_pinned_version(capsys):
    PipSize(quiet=True, as_bytes=True, no_deps=True).run("root==1.0")
    assert capsys.readouterr().out.strip() == "100"


def test_pin_to_missing_version_fails(capsys):
    assert PipSize(quiet=True).run("root==9.9") is False


def test_marker_for_other_platform_is_skipped(capsys):
    PipSize(as_json=True).run("root")
    names = {d["name"] for d in json.loads(capsys.readouterr().out)["dependencies"]}
    assert "winonly" not in names


def test_extra_is_opt_in(capsys):
    PipSize(quiet=True, as_bytes=True).run("root[fancy]")
    assert capsys.readouterr().out.strip() == str(1000 + 200 + 50 + 10)


def test_extras_glob_filter(capsys):
    PipSize(quiet=True, as_bytes=True, extras_filter={"fan*"}).run("root")
    assert capsys.readouterr().out.strip() == str(1000 + 200 + 50 + 10)


def test_all_extras_covers_the_root_only(capsys):
    PipSize(quiet=True, as_bytes=True, all_extras=True).run("root")
    assert capsys.readouterr().out.strip() == str(1000 + 200 + 50 + 10)     # fancy yes, child[dev] no


def test_all_extras_deep_reaches_dependencies(capsys):
    PipSize(quiet=True, as_bytes=True, all_extras_deep=True).run("root")
    assert capsys.readouterr().out.strip() == str(1000 + 200 + 50 + 10 + 7)


def test_all_extras_still_honours_platform_markers(capsys):
    PipSize(as_json=True, all_extras_deep=True).run("root")
    names = {d["name"] for d in json.loads(capsys.readouterr().out)["dependencies"]}
    assert "winonly" not in names


def test_json_output_is_pure_json(capsys):
    """Regression: header / progress lines used to corrupt --json output."""
    PipSize(as_json=True).run("root")
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "root" and data["version"] == "2.0"
    assert data["total_size_bytes"] == 1250 and data["size_bytes"] == 1000


def test_tree_order_is_deterministic(capsys):
    PipSize(as_json=True).run("root[fancy]")
    names = [d["name"] for d in json.loads(capsys.readouterr().out)["dependencies"]]
    assert names == sorted(names, key=str.lower)


def test_unresolvable_package_returns_false(capsys):
    assert PipSize(quiet=True).run("does-not-exist") is False


def test_unresolvable_prints_reason_to_stderr(capsys):
    assert PipSize().run("does-not-exist") is False
    assert "Could not resolve" in capsys.readouterr().err


def test_conflict_is_reported(capsys):
    PipSize(as_json=True).run("conflict-root")
    data = json.loads(capsys.readouterr().out)
    assert [c["name"] for c in data["conflicts"]] == ["shared"]
    assert data["conflicts"][0]["required_by"] == "conf-b"


def test_conflict_shows_in_tree(capsys):
    PipSize().run("conflict-root")
    assert "Version conflicts" in capsys.readouterr().out


def test_skipped_tree_output(capsys):
    PipSize(skip="child").run("root")
    out = capsys.readouterr().out
    assert "[skipped: skip list]" in out
    assert "Total to download" in out and "Skipped" in out


# ── package / selector units ─────────────────────────────────────────

def test_package_info_totals_and_walk():
    leaf = PackageInfo("leaf", "1", 5, "l.whl", skipped="installed 1")
    mid = PackageInfo("mid", "1", 20, "m.whl", dependencies=[leaf])
    root = PackageInfo("root", "1", 100, "r.whl", dependencies=[mid])
    assert root.total_size() == 120
    assert root.total_size(include_skipped=True) == 125
    assert root.skipped_size() == 5
    assert [p.name for p in root.walk()] == ["root", "mid", "leaf"]


def test_wheel_selector_prefers_wheel_over_sdist():
    files = [
        {"filename": "x-1.tar.gz", "size": 1, "requires_python": None},
        {"filename": "x-1-py3-none-any.whl", "size": 2, "requires_python": None},
    ]
    assert WheelSelector.best(files)["filename"].endswith(".whl")


def test_wheel_selector_skips_incompatible_python():
    files = [{"filename": "x-1-py3-none-any.whl", "size": 2, "requires_python": "<3"}]
    assert WheelSelector.best(files) is None


def test_wheel_selector_ignores_yanked_unless_allowed():
    files = [{"filename": "x-1-py3-none-any.whl", "size": 2, "yanked": True}]
    assert WheelSelector.best(files) is None
    assert WheelSelector.best(files, allow_yanked=True) is not None


def _rel(name, version, **kw):
    return [{"filename": f"{name}-{version}-py3-none-any.whl", "size": 1, "requires_python": kw.get("rp"),
             "yanked": kw.get("yanked", False)}]


def test_pick_release_highest_stable():
    versions = {"1.0": _rel("x", "1.0"), "2.0": _rel("x", "2.0"), "3.0rc1": _rel("x", "3.0rc1")}
    assert pick_release(versions, SpecifierSet())[0] == "2.0"
    assert pick_release(versions, SpecifierSet("<2"))[0] == "1.0"


def test_pick_release_prerelease_only_when_needed():
    versions = {"3.0rc1": _rel("x", "3.0rc1")}
    assert pick_release(versions, SpecifierSet())[0] == "3.0rc1"          # nothing else matches
    versions["1.0"] = _rel("x", "1.0")
    assert pick_release(versions, SpecifierSet())[0] == "1.0"
    assert pick_release(versions, SpecifierSet(">=3.0rc1"))[0] == "3.0rc1"  # explicitly requested


def test_pick_release_skips_yanked_and_python_incompatible():
    versions = {
        "1.0": _rel("x", "1.0"),
        "2.0": _rel("x", "2.0", yanked=True),
        "3.0": _rel("x", "3.0", rp="<3"),
    }
    assert pick_release(versions, SpecifierSet())[0] == "1.0"


def test_pick_release_no_match():
    assert pick_release({"1.0": _rel("x", "1.0")}, SpecifierSet(">=5")) is None


def test_pick_release_matching_version_without_usable_file():
    versions = {"1.0": _rel("x", "1.0", rp="<3")}
    assert pick_release(versions, SpecifierSet()) == ("1.0", None)


def test_group_by_version_keeps_wheels_and_sdists_only():
    files = [
        {"filename": "x-1.0-py3-none-any.whl", "size": 10},
        {"filename": "x-1.0.tar.gz", "size": 20, "requires-python": ">=3.8", "yanked": "bad"},
        {"filename": "x-1.0-py2.7.egg", "size": 5},
        {"filename": "x-1.0.win32.exe", "size": 5},
        {"filename": "garbage.whl", "size": 5},
    ]
    grouped = _group_by_version(files)
    assert list(grouped) == ["1.0"] and len(grouped["1.0"]) == 2
    sdist = [f for f in grouped["1.0"] if f["filename"].endswith(".tar.gz")][0]
    assert sdist["requires_python"] == ">=3.8" and sdist["yanked"] is True


def test_cache_roundtrip_and_clear():
    url = "https://pypi.org/pypi/demo/json"
    assert Cache.read(url) is None
    Cache.write(url, {"a": 1})
    assert Cache.read(url) == {"a": 1}
    assert Cache.clear() == 1
    assert Cache.read(url) is None


def test_cache_keys_do_not_collide():
    a = Cache._path("https://pypi.org/simple/requests/")
    b = Cache._path("https://pypi.org/pypi/requests/2.0/json")
    assert a != b
