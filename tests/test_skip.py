import os
import stat
import sys

import pytest

from pip_size.errors import SkipSourceError
from pip_size.skip import SkipRules, installed_packages, parse_freeze, read_freeze_file


def test_parse_freeze_formats():
    text = """
    # comment
    numpy==1.26.4
    Pandas_Stubs==2.0.0  # trailing comment
    requests>=2
    mylib @ file:///tmp/mylib.whl
    -e git+https://example.com/x.git#egg=x
    --hash=sha256:abc
    bare-name
    not a requirement !!
    """
    parsed = parse_freeze(text)
    assert parsed == {
        "numpy": "1.26.4",
        "pandas-stubs": "2.0.0",
        "requests": None,
        "mylib": None,
        "bare-name": None,
    }


def test_names_and_globs_are_normalised():
    rules = SkipRules(["NumPy, nvidia-*", "typing_extensions"])
    assert rules.match("numpy") == "skip list"
    assert rules.match("nvidia-cublas-cu12") == "skip list"
    assert rules.match("Typing.Extensions") == "skip list"
    assert rules.match("scipy") is None


def test_installed_version_must_satisfy_the_requirement():
    rules = SkipRules(installed={"numpy": "1.26.4"})
    assert rules.match("numpy", ">=1.20") == "installed 1.26.4"
    assert rules.match("numpy", "") == "installed 1.26.4"
    assert rules.match("numpy", ">=2") is None            # pip would upgrade it, so it still counts


def test_installed_without_version_always_matches():
    assert SkipRules(installed={"mylib": None}).match("mylib", ">=99") == "already installed"


def test_empty_rules_are_falsy():
    assert not SkipRules()
    assert SkipRules("x") and SkipRules(installed={"x": "1"})


def test_installed_packages_current_env_contains_pytest():
    assert "pytest" in installed_packages()


@pytest.mark.skipif(os.name == "nt", reason="needs a shell wrapper")
def test_installed_packages_from_other_interpreter(tmp_path):
    wrapper = tmp_path / "python-wrapper"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    assert "pytest" in installed_packages(str(wrapper))


def test_installed_packages_bad_interpreter():
    with pytest.raises(SkipSourceError):
        installed_packages("/definitely/not/a/python")


def test_read_freeze_file_and_missing(tmp_path):
    f = tmp_path / "freeze.txt"
    f.write_text("a==1\nb==2\n")
    assert read_freeze_file(str(f)) == {"a": "1", "b": "2"}
    with pytest.raises(SkipSourceError):
        read_freeze_file(str(tmp_path / "nope.txt"))


def test_build_combines_all_sources(tmp_path):
    f = tmp_path / "freeze.txt"
    f.write_text("fromfile==3\n")
    rules = SkipRules.build(names="byname", skip_installed=True, freeze_files=[str(f)])
    assert rules.match("byname") == "skip list"
    assert rules.match("fromfile") == "installed 3"
    assert rules.match("pytest") is not None
