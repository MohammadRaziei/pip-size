import asyncio
import shutil
import subprocess
import sys

import pytest

import pip_size
from pip_size import BuildError, PipSize
from pip_size import build as build_mod
from pip_size.build import SourceSpec, _copy_of, builder_command, parse_source_spec

pytestmark = pytest.mark.usefixtures("fake_pypi")


@pytest.fixture
def project(tmp_path):
    """A directory that looks like an installable project."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'mypkg'\nversion = '1.0'\n")
    return root


@pytest.fixture
def fake_builder(monkeypatch, make_wheel, tmp_path):
    """Replace the real builder subprocess: 'building' drops a prebuilt wheel into the output dir."""
    src = tmp_path / "prebuilt"
    src.mkdir()
    import zipfile

    wheel = src / "mypkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("mypkg-1.0.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: mypkg\nVersion: 1.0\nProvides-Extra: fancy\n"
                    "Requires-Dist: child>=1\nRequires-Dist: extra-dep; extra == 'fancy'\n")
        zf.writestr("mypkg/__init__.py", "")

    calls = []

    async def execute(cmd, cwd, timeout):
        calls.append(cmd)
        out_dir = cmd[cmd.index("-w") + 1] if "-w" in cmd else cmd[cmd.index("--out-dir") + 1]
        shutil.copy(wheel, out_dir)
        return 0, ""

    monkeypatch.setattr(build_mod, "_execute", execute)
    monkeypatch.setattr(build_mod, "_has_pip", lambda: True)
    execute.calls, execute.wheel = calls, wheel
    return execute


# ── recognising sources ──────────────────────────────────────────────

def test_plain_requirements_are_not_sources():
    for spec in ("requests", "requests>=2,<3", "requests[socks]", "pkg; python_version < '3.9'"):
        assert parse_source_spec(spec) is None


def test_garbage_is_not_a_source():
    assert parse_source_spec("not a valid req!!") is None


def test_vcs_urls():
    src = parse_source_spec("git+https://example.com/o/r.git@v1#subdirectory=pkg")
    assert (src.kind, src.target) == ("vcs", "git+https://example.com/o/r.git@v1#subdirectory=pkg")
    assert parse_source_spec("hg+ssh://example.com/r").kind == "vcs"


def test_direct_references_keep_their_extras():
    src = parse_source_spec("mypkg[fancy,other] @ git+https://example.com/o/r.git")
    assert (src.kind, src.extras, src.target) == ("vcs", {"fancy", "other"}, "git+https://example.com/o/r.git")
    assert parse_source_spec("mypkg @ https://example.com/mypkg-1.0.tar.gz").kind == "url"


def test_directory_with_extras(project):
    src = parse_source_spec(f"{project}[fancy]")
    assert (src.kind, src.target, src.extras) == ("directory", str(project), {"fancy"})


def test_bare_name_of_an_existing_directory_is_a_package_name(project, monkeypatch):
    monkeypatch.chdir(project.parent)
    assert parse_source_spec("proj") is None          # like pip: needs ./ or a path separator
    assert parse_source_spec("./proj").kind == "directory"


def test_directory_must_be_a_project(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(BuildError, match="not an installable project"):
        parse_source_spec(str(tmp_path / "empty"))


def test_missing_path_is_a_clear_error(tmp_path):
    with pytest.raises(BuildError, match="path not found"):
        parse_source_spec(str(tmp_path / "nope"))


def test_archive(tmp_path):
    archive = tmp_path / "mypkg-1.0.tar.gz"
    archive.write_bytes(b"x")
    assert parse_source_spec(str(archive)).kind == "archive"
    other = tmp_path / "notes.txt"
    other.write_text("x")
    with pytest.raises(BuildError, match="path not found, or not a directory or sdist"):
        parse_source_spec(str(other))


# ── choosing a builder ───────────────────────────────────────────────

def test_pip_is_preferred(monkeypatch, tmp_path):
    monkeypatch.setattr(build_mod, "_has_pip", lambda: True)
    cmd = builder_command(SourceSpec("/x", "directory", "/x"), tmp_path, "/x")
    assert cmd[:4] == [sys.executable, "-m", "pip", "wheel"] and "--no-deps" in cmd and "/x" == cmd[-1]


def test_uv_fallback_for_directories_only(monkeypatch, tmp_path):
    monkeypatch.setattr(build_mod, "_has_pip", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    assert builder_command(SourceSpec("/x", "directory", "/x"), tmp_path, "/x")[1:3] == ["build", "--wheel"]
    with pytest.raises(BuildError, match="needs pip"):
        builder_command(SourceSpec("git+https://h/r", "vcs", "git+https://h/r"), tmp_path, "git+https://h/r")


def test_no_builder_at_all(monkeypatch, tmp_path):
    monkeypatch.setattr(build_mod, "_has_pip", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(BuildError, match="no wheel builder available"):
        builder_command(SourceSpec("/x", "directory", "/x"), tmp_path, "/x")


# ── building and resolving ───────────────────────────────────────────

def test_directory_is_built_and_sized_like_a_wheel(project, fake_builder):
    res = pip_size.resolve(str(project))
    assert res.root.local and res.root.built_from == str(project)
    assert res.root.size == fake_builder.wheel.stat().st_size
    assert res.total_size == fake_builder.wheel.stat().st_size + 200 + 50     # child + leaf


def test_build_uses_a_copy_and_leaves_the_tree_alone(project, fake_builder):
    before = sorted(p.name for p in project.iterdir())
    pip_size.resolve(str(project))
    built_from = fake_builder.calls[0][-1]
    assert built_from != str(project)                       # a throw-away copy was built
    assert sorted(p.name for p in project.iterdir()) == before


def test_extras_on_a_directory(project, fake_builder):
    res = pip_size.resolve(f"{project}[fancy]")
    assert res.total_size == fake_builder.wheel.stat().st_size + 200 + 50 + 10


def test_vcs_url_and_direct_reference(fake_builder):
    res = pip_size.resolve("git+https://example.com/o/r.git")
    assert res.root.built_from == "git+https://example.com/o/r.git"
    assert fake_builder.calls[-1][-1] == "git+https://example.com/o/r.git"
    res = pip_size.resolve("mypkg[fancy] @ git+https://example.com/o/r.git")
    assert res.total_size == fake_builder.wheel.stat().st_size + 260


def test_skip_and_no_deps_apply_to_built_wheels(project, fake_builder):
    size = fake_builder.wheel.stat().st_size
    assert pip_size.resolve(str(project), no_deps=True).total_size == size
    assert pip_size.resolve(str(project), skip=["child"]).total_size == size


def test_progress_reports_the_build(project, fake_builder):
    lines = []
    pip_size.resolve(str(project), progress=lines.append)
    assert any("building a wheel from" in line for line in lines)
    assert any("(built from source)" in line for line in lines)


def test_a_failed_build_shows_the_tail_of_the_output(project, monkeypatch):
    async def failing(cmd, cwd, timeout):
        return 1, "\n".join(f"line {i}" for i in range(40)) + "\nerror: boom"

    monkeypatch.setattr(build_mod, "_execute", failing)
    monkeypatch.setattr(build_mod, "_has_pip", lambda: True)
    with pytest.raises(BuildError, match=r"failed \(exit status 1\)") as exc:
        pip_size.resolve(str(project))
    assert "error: boom" in str(exc.value) and "line 0\n" not in str(exc.value)


def test_a_build_without_a_wheel_is_an_error(project, monkeypatch):
    async def nothing(cmd, cwd, timeout):
        return 0, ""

    monkeypatch.setattr(build_mod, "_execute", nothing)
    monkeypatch.setattr(build_mod, "_has_pip", lambda: True)
    with pytest.raises(BuildError, match="produced no wheel"):
        pip_size.resolve(str(project))


def test_build_errors_are_pipsize_errors(project):
    assert issubclass(BuildError, pip_size.PipSizeError)


def test_copy_skips_heavy_directories_but_keeps_git(project, tmp_path):
    for name in (".venv", "node_modules", "build", "dist", "pkg.egg-info", ".git"):
        (project / name).mkdir()
        (project / name / "f").write_text("x")
    (project / "src").mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    with _copy_of(project, workdir) as copied:
        names = {p.name for p in copied.iterdir()}
    assert names == {".git", "src", "pyproject.toml"}


# ── presentation ─────────────────────────────────────────────────────

def test_tree_and_json_show_the_source(project, fake_builder, capsys):
    PipSize().run(str(project))
    assert "[built from source]" in capsys.readouterr().out
    PipSize(as_json=True).run(str(project))
    import json

    assert json.loads(capsys.readouterr().out)["built_from"] == str(project)


def test_cli_reports_a_build_failure_cleanly(project, monkeypatch, capsys):
    async def failing(cmd, cwd, timeout):
        return 1, "error: boom"

    monkeypatch.setattr(build_mod, "_execute", failing)
    monkeypatch.setattr(build_mod, "_has_pip", lambda: True)
    assert PipSize().run(str(project)) is False
    assert "failed (exit status 1)" in capsys.readouterr().err


def test_a_real_build_when_pip_is_available(tmp_path):
    """End to end with the real builder, on a tiny setuptools-free project, offline."""
    pytest.importorskip("pip")
    proj = tmp_path / "tiny"
    (proj / "tiny").mkdir(parents=True)
    (proj / "tiny" / "__init__.py").write_text("")
    (proj / "pyproject.toml").write_text(
        "[build-system]\nrequires = []\nbuild-backend = 'backend'\nbackend-path = ['_b']\n"
        "[project]\nname = 'tiny'\nversion = '1.0'\ndependencies = ['child>=1']\n"
    )
    (proj / "_b").mkdir()
    (proj / "_b" / "backend.py").write_text(
        "import zipfile, os\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    name = 'tiny-1.0-py3-none-any.whl'\n"
        "    with zipfile.ZipFile(os.path.join(wheel_directory, name), 'w') as z:\n"
        "        z.writestr('tiny-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\\nName: tiny\\nVersion: 1.0\\nRequires-Dist: child>=1\\n')\n"
        "        z.writestr('tiny-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\\nGenerator: t\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')\n"
        "        z.writestr('tiny-1.0.dist-info/RECORD', '')\n"
        "        z.writestr('tiny/__init__.py', '')\n"
        "    return name\n"
        "def get_requires_for_build_wheel(config_settings=None):\n    return []\n"
    )
    res = pip_size.resolve(str(proj))
    assert res.root.name == "tiny" and res.root.built_from == str(proj)
    assert [d.name for d in res.root.dependencies] == ["child"]
    assert subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0
