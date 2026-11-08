import json
import zipfile

import pytest

from pip_size import PipSize, WheelError, read_wheel
from pip_size.wheel import parse_wheel_spec


def test_parse_wheel_spec():
    path, extras = parse_wheel_spec("./dist/foo-1.0-py3-none-any.whl[a, b]")
    assert path.name == "foo-1.0-py3-none-any.whl" and extras == {"a", "b"}
    assert parse_wheel_spec("dist/foo-1.0-py3-none-any.whl")[1] == set()
    assert parse_wheel_spec("requests") is None
    assert parse_wheel_spec("requests[security]") is None


def test_read_wheel_metadata(make_wheel):
    path = make_wheel(requires=["child>=1", "extra-dep; extra == 'fancy'"], extras=["fancy"])
    wheel = read_wheel(path)
    assert (wheel.name, wheel.version) == ("foo", "1.0")
    assert wheel.size == path.stat().st_size
    assert wheel.filename == path.name
    assert wheel.requires_dist == ["child>=1", "extra-dep; extra == 'fancy'"]
    assert wheel.provides_extra == ["fancy"]


def test_read_wheel_errors(tmp_path, make_wheel):
    with pytest.raises(WheelError, match="not found"):
        read_wheel(tmp_path / "missing-1.0-py3-none-any.whl")

    bad = tmp_path / "bad-1.0-py3-none-any.whl"
    bad.write_text("not a zip")
    with pytest.raises(WheelError, match="not a valid wheel"):
        read_wheel(bad)

    empty = tmp_path / "empty-1.0-py3-none-any.whl"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("empty/__init__.py", "")
    with pytest.raises(WheelError, match="METADATA"):
        read_wheel(empty)


def test_read_wheel_falls_back_to_filename(tmp_path):
    path = tmp_path / "bar-2.5-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bar-2.5.dist-info/METADATA", "Metadata-Version: 2.1\n")
    wheel = read_wheel(path)
    assert (wheel.name, wheel.version) == ("bar", "2.5")


@pytest.mark.usefixtures("fake_pypi")
class TestResolveLocalWheel:
    def test_size_is_the_file_and_deps_come_from_pypi(self, make_wheel, capsys):
        path = make_wheel(requires=["child>=1"])
        PipSize(quiet=True, as_bytes=True).run(str(path))
        assert capsys.readouterr().out.strip() == str(path.stat().st_size + 200 + 50)

    def test_extras_on_the_path(self, make_wheel, capsys):
        path = make_wheel(requires=["child>=1", "extra-dep; extra == 'fancy'"], extras=["fancy"])
        PipSize(quiet=True, as_bytes=True).run(f"{path}[fancy]")
        assert capsys.readouterr().out.strip() == str(path.stat().st_size + 200 + 50 + 10)

    def test_no_deps(self, make_wheel, capsys):
        path = make_wheel(requires=["child>=1"])
        PipSize(quiet=True, as_bytes=True, no_deps=True).run(str(path))
        assert capsys.readouterr().out.strip() == str(path.stat().st_size)

    def test_json_marks_local(self, make_wheel, capsys):
        path = make_wheel(requires=["child>=1"])
        PipSize(as_json=True).run(str(path))
        data = json.loads(capsys.readouterr().out)
        assert data["local"] is True and data["size_bytes"] == path.stat().st_size
        assert data["dependencies"][0]["name"] == "child"

    def test_marker_conditions_apply(self, make_wheel, capsys):
        path = make_wheel(requires=["winonly; sys_platform == 'nonexistent-os'"])
        PipSize(quiet=True, as_bytes=True).run(str(path))
        assert capsys.readouterr().out.strip() == str(path.stat().st_size)

    def test_skip_applies_to_local_wheel_deps(self, make_wheel, capsys):
        path = make_wheel(requires=["child>=1"])
        PipSize(quiet=True, as_bytes=True, skip="child").run(str(path))
        assert capsys.readouterr().out.strip() == str(path.stat().st_size)

    def test_missing_wheel_returns_false(self, tmp_path, capsys):
        assert PipSize(quiet=True).run(str(tmp_path / "nope-1.0-py3-none-any.whl")) is False
