import io
import json
import sys

import pytest

from pip_size.__main__ import main

pytestmark = pytest.mark.usefixtures("fake_pypi")


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["pip-size", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    return exc.value.code


def ok_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["pip-size", *argv])
    main()


def test_clear_cache_exits_zero(monkeypatch):
    assert run_cli(monkeypatch, "--clear-cache") == 0


def test_missing_package_is_usage_error(monkeypatch):
    assert run_cli(monkeypatch) == 2


def test_unknown_package_exits_nonzero(monkeypatch):
    assert run_cli(monkeypatch, "does-not-exist", "--quiet") == 1


def test_success_does_not_exit(monkeypatch, capsys):
    ok_cli(monkeypatch, "root", "--json")
    assert json.loads(capsys.readouterr().out)["name"] == "root"


def test_skip_option_repeatable_and_comma_separated(monkeypatch, capsys):
    ok_cli(monkeypatch, "root", "--skip", "child", "--skip", "leaf", "--quiet", "--bytes")
    assert capsys.readouterr().out.strip() == "1000"


def test_skip_json_reports_skipped(monkeypatch, capsys):
    ok_cli(monkeypatch, "root", "--skip", "child", "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["skipped_size_bytes"] == 200 and data["skipped_count"] == 1
    assert data["total_size_bytes"] == 1000
    assert data["dependencies"][0]["skipped"] == "skip list"


def test_skip_file_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("child==1.5\nleaf==0.1\n"))
    ok_cli(monkeypatch, "root", "--skip-file", "-", "--quiet", "--bytes")
    assert capsys.readouterr().out.strip() == "1000"


def test_skip_installed_flag(monkeypatch, capsys):
    monkeypatch.setattr("pip_size.skip.installed_packages", lambda python=None: {"child": "1.5"})
    ok_cli(monkeypatch, "root", "--skip-installed", "--quiet", "--bytes")
    assert capsys.readouterr().out.strip() == "1000"


def test_installed_from_implies_skip_installed(monkeypatch, capsys):
    seen = []

    def fake(python=None):
        seen.append(python)
        return {"child": "1.5"}

    monkeypatch.setattr("pip_size.skip.installed_packages", fake)
    ok_cli(monkeypatch, "root", "--installed-from", "/opt/venv/bin/python", "--quiet", "--bytes")
    assert seen == ["/opt/venv/bin/python"] and capsys.readouterr().out.strip() == "1000"


def test_bad_skip_file_is_a_clean_error(monkeypatch, capsys, tmp_path):
    assert run_cli(monkeypatch, "root", "--skip-file", str(tmp_path / "nope.txt")) == 1
    assert "cannot read skip file" in capsys.readouterr().err


def test_local_wheel_via_cli(monkeypatch, capsys, make_wheel):
    path = make_wheel(requires=["child>=1"])
    ok_cli(monkeypatch, str(path), "--quiet", "--bytes")
    assert capsys.readouterr().out.strip() == str(path.stat().st_size + 250)


def test_bad_wheel_is_a_clean_error(monkeypatch, capsys, tmp_path):
    bad = tmp_path / "bad-1.0-py3-none-any.whl"
    bad.write_text("nope")
    assert run_cli(monkeypatch, str(bad)) == 1
    assert "not a valid wheel" in capsys.readouterr().err


def test_all_extras_flags(monkeypatch, capsys):
    ok_cli(monkeypatch, "root", "-a", "--quiet", "--bytes")
    assert capsys.readouterr().out.strip() == "1260"
    ok_cli(monkeypatch, "root", "--all-extras-deep", "--quiet", "--bytes")
    assert capsys.readouterr().out.strip() == "1267"


def test_version_flag(monkeypatch, capsys):
    from pip_size import __version__

    assert run_cli(monkeypatch, "--version") == 0
    assert capsys.readouterr().out.strip() == f"pip-size {__version__}"
