import asyncio

import pytest
from packaging.requirements import Requirement

import pip_size
from pip_size import ResolutionError, SkipRules, WheelError

pytestmark = pytest.mark.usefixtures("fake_pypi")


def test_resolve_returns_data_and_prints_nothing(capsys):
    res = pip_size.resolve("root")
    assert capsys.readouterr().out == ""
    assert res.total_size == 1250
    assert res.skipped_size == 0
    assert [p.name for p in res.packages] == ["root", "child", "leaf"]
    assert res.root.version == "2.0"


def test_resolve_async():
    res = asyncio.run(pip_size.resolve_async("root==1.0"))
    assert res.total_size == 100


def test_resolve_accepts_requirement_and_pathlike(make_wheel):
    assert pip_size.resolve(Requirement("root<2")).root.version == "1.0"
    path = make_wheel(requires=["leaf"])
    assert pip_size.resolve(path).total_size == path.stat().st_size + 50      # os.PathLike


def test_resolve_options():
    assert pip_size.resolve("root", no_deps=True).total_size == 1000
    assert pip_size.resolve("root", extras="fancy").total_size == 1260
    assert pip_size.resolve("root", extras=["fan*"]).total_size == 1260


def test_all_extras_options():
    assert pip_size.resolve("root", all_extras=True).total_size == 1260
    assert pip_size.resolve("root", all_extras_deep=True).total_size == 1267


def test_progress_callback_receives_lines():
    lines = []
    pip_size.resolve("root", progress=lines.append)
    assert any("root==2.0" in line for line in lines)


def test_skip_keeps_package_in_tree_but_not_in_total():
    res = pip_size.resolve("root", skip=["child"])
    assert res.total_size == 1000
    assert res.skipped_size == 250 - 50         # child only; its own deps are not expanded
    assert [p.name for p in res.packages] == ["root", "child"]
    child = res.skipped[0]
    assert child.name == "child" and child.size == 200 and child.skipped == "skip list"


def test_skip_installed_uses_this_environment(monkeypatch):
    monkeypatch.setattr("pip_size.skip.installed_packages", lambda python=None: {"child": "1.9", "leaf": "9.9"})
    res = pip_size.resolve("root", skip_installed=True)
    assert [p.skipped for p in res.packages] == [None, "installed 1.9"]
    assert res.total_size == 1000


def test_skip_installed_version_mismatch_is_not_skipped(monkeypatch):
    monkeypatch.setattr("pip_size.skip.installed_packages", lambda python=None: {"child": "0.1"})   # needs >=1
    assert pip_size.resolve("root", skip_installed=True).total_size == 1250


def test_skip_files(tmp_path):
    freeze = tmp_path / "freeze.txt"
    freeze.write_text("child==1.5\n")
    res = pip_size.resolve("root", skip_files=[str(freeze)])
    assert res.skipped[0].skipped == "installed 1.5"


def test_prebuilt_skip_rules():
    res = pip_size.resolve("root", skip_rules=SkipRules("leaf"))
    assert res.total_size == 1200 and res.skipped[0].name == "leaf"


def test_root_is_never_skipped():
    assert pip_size.resolve("root", skip=["root"]).total_size == 1250


def test_diamond_dependency_counted_once():
    res = pip_size.resolve("conflict-root")
    assert [p.name for p in res.packages].count("shared") == 1


def test_conflicts_attribute():
    res = pip_size.resolve("conflict-root")
    assert len(res.conflicts) == 1 and res.conflicts[0].required_by == "conf-b"
    assert pip_size.resolve("root").conflicts == []


def test_to_dict_is_plain_numeric_data():
    data = pip_size.resolve("root", skip=["leaf"]).to_dict()
    assert data["size"] == 1000 and data["total_size"] == 1200 and data["skipped_size"] == 50
    assert data["dependencies"][0]["dependencies"][0]["skipped"] == "skip list"


def test_errors_are_exceptions_not_none():
    with pytest.raises(ResolutionError, match="does-not-exist"):
        pip_size.resolve("does-not-exist")
    with pytest.raises(ResolutionError, match="Invalid requirement"):
        pip_size.resolve("not a valid req!!")
    with pytest.raises(WheelError):
        pip_size.resolve("./missing-1.0-py3-none-any.whl")


def test_broken_dependency_becomes_a_warning_not_a_failure(monkeypatch):
    from conftest import FAKE_PYPI

    monkeypatch.setitem(FAKE_PYPI, "needs-ghost", {"1.0": (5, ["ghost-package"])})
    res = pip_size.resolve("needs-ghost")
    assert res.total_size == 5
    assert any("ghost-package" in w for w in res.warnings)
