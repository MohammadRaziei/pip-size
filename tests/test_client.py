"""PyPIClient against a mocked HTTP layer (these tests deliberately do NOT use the fake_pypi fixture)."""

import asyncio

from pip_size.client import PyPIClient


def test_client_parses_simple_api_and_caches(monkeypatch):
    calls = []

    async def fake_get(self, url, accept):
        calls.append((url, accept))
        return {"files": [
            {"filename": "demo-1.0-py3-none-any.whl", "size": 7, "requires-python": ">=3.8", "yanked": False},
            {"filename": "demo-1.0.tar.gz", "size": 9},
        ]}

    monkeypatch.setattr(PyPIClient, "_get", fake_get)

    async def go():
        client = PyPIClient()
        first = await client.project_files("Demo_Pkg")
        second = await client.project_files("Demo_Pkg")      # served from cache
        return first, second

    first, second = asyncio.run(go())
    assert first == second and first["1.0"][0]["requires_python"] == ">=3.8"
    assert len(calls) == 1
    assert calls[0][0] == "https://pypi.org/simple/demo-pkg/"
    assert calls[0][1] == "application/vnd.pypi.simple.v1+json"


def test_client_release_extracts_requires_dist(monkeypatch):

    async def fake_get(self, url, accept):
        assert url == "https://pypi.org/pypi/demo/1.0/json"
        return {"info": {"requires_dist": ["a>=1", "b"]},
                "urls": [{"filename": "demo-1.0-py3-none-any.whl", "size": 3}]}

    monkeypatch.setattr(PyPIClient, "_get", fake_get)
    data = asyncio.run(PyPIClient(use_cache=False).release("demo", "1.0"))
    assert data["requires_dist"] == ["a>=1", "b"] and data["files"][0]["size"] == 3
