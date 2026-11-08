import zipfile

import pytest
from packaging.utils import canonicalize_name

from pip_size.client import PyPIClient


def _file(name, version, size, **kw):
    return {
        "filename": f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
        "size": size, "requires_python": kw.get("requires_python"), "yanked": kw.get("yanked", False),
    }


# name -> version -> (size, requires_dist)
FAKE_PYPI = {
    "root": {
        "1.0": (100, []),
        "2.0": (1000, ["child>=1", "extra-dep; extra == 'fancy'", "winonly; sys_platform == 'nonexistent-os'"]),
    },
    "child":     {"1.5": (200, ["leaf"])},
    "leaf":      {"0.1": (50, [])},
    "extra-dep": {"3.0": (10, [])},
    "winonly":   {"1.0": (9999, [])},
    # diamond with incompatible constraints on `shared`
    "conflict-root": {"1.0": (1, ["conf-a", "conf-b"])},
    "conf-a": {"1.0": (2, ["shared>=2"])},
    "conf-b": {"1.0": (3, ["shared<2"])},
    "shared": {"1.0": (40, []), "2.0": (400, [])},
}


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """No test ever touches the real cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))


@pytest.fixture
def fake_pypi(monkeypatch):
    """Replace the network layer of PyPIClient with the in-memory FAKE_PYPI."""

    async def project_files(self, name):
        try:
            releases = FAKE_PYPI[canonicalize_name(name)]
        except KeyError:
            raise RuntimeError(f"HTTP 404: Not Found ({name})") from None
        return {v: [_file(name, v, size)] for v, (size, _) in releases.items()}

    async def release(self, name, version):
        try:
            size, requires = FAKE_PYPI[canonicalize_name(name)][version]
        except KeyError:
            raise RuntimeError(f"HTTP 404: Not Found ({name} {version})") from None
        return {"requires_dist": requires, "files": [_file(name, version, size)]}

    async def aenter(self):
        return self

    async def aexit(self, *_):
        return None

    monkeypatch.setattr(PyPIClient, "project_files", project_files)
    monkeypatch.setattr(PyPIClient, "release", release)
    monkeypatch.setattr(PyPIClient, "__aenter__", aenter)
    monkeypatch.setattr(PyPIClient, "__aexit__", aexit)


@pytest.fixture
def make_wheel(tmp_path):
    """Build a minimal valid .whl on disk."""

    def build(name="foo", version="1.0", requires=(), extras=(), filename=None, metadata=None):
        path = tmp_path / (filename or f"{name}-{version}-py3-none-any.whl")
        lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        lines += [f"Provides-Extra: {e}" for e in extras]
        lines += [f"Requires-Dist: {r}" for r in requires]
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"{name}-{version}.dist-info/METADATA", metadata or "\n".join(lines) + "\n")
            zf.writestr(f"{name}/__init__.py", "")
        return path

    return build
