# Recipes

## Fail a build when the wheel is too big

```python
import glob
import pip_size

wheel = sorted(glob.glob("dist/mypkg-*.whl"))[-1]
res = pip_size.resolve(wheel, skip_installed=True)

BUDGET = 5_000_000
assert res.total_size < BUDGET, f"install size {res.total_size} bytes exceeds {BUDGET}"
```

`skip_installed=True` leaves out whatever your CI environment already has. Drop it to count
everything. For a shell version, see [Use in CI](../guide/ci.md).

## Compare candidates

```python
import asyncio
import pip_size
from pip_size import Printer

async def compare(names):
    results = await asyncio.gather(*(pip_size.resolve_async(n) for n in names))
    for name, res in sorted(zip(names, results), key=lambda item: item[1].total_size):
        print(f"{name:<10} {Printer.format_size(res.total_size):>10}  ({len(res.packages)} packages)")

asyncio.run(compare(["requests", "httpx", "aiohttp"]))
```

The numbers depend on the day's PyPI releases and on your platform.

## Find the heaviest contributors

```python
from pip_size import Printer

res = pip_size.resolve("httpx[http2]")
for pkg in sorted(res.packages, key=lambda p: p.size, reverse=True)[:3]:
    print(f"{pkg.name:<20} {Printer.format_size(pkg.size)}")
```

## Follow the heaviest branch

```python
def heaviest_branch(pkg):
    deps = pkg.dependencies
    return [pkg.name] + (heaviest_branch(max(deps, key=lambda d: d.total_size())) if deps else [])

" -> ".join(heaviest_branch(pip_size.resolve("httpx[http2]").root))
```

## Export to JSON

```python
import json

with open("size-report.json", "w") as f:
    json.dump(pip_size.resolve("requests").to_dict(), f, indent=2)
```

## Size against a deployment image

Take a freeze from the image once, then size packages against it:

```bash
docker run --rm my-base-image pip freeze > base-freeze.txt
```

```python
res = pip_size.resolve("fastapi", skip_files=["base-freeze.txt"])
res.total_size      # only what the image is missing
```

## Reuse skip rules for many packages

```python
from pip_size import SkipRules

rules = SkipRules.build(skip_installed=True)       # lists installed packages once
sizes = {name: pip_size.resolve(name, skip_rules=rules).total_size
         for name in ["requests", "httpx", "fastapi"]}
```
