# Proxies and cache

## Proxies

`pip-size` talks only to `pypi.org`. If you are behind a proxy, give it one with `--proxy` or the
usual environment variables.

```bash
pip-size requests --proxy http://user:pass@host:8080
pip-size requests --proxy socks5://host:1080         # needs pip-size[socks]
HTTPS_PROXY=http://host:8080 pip-size requests
```

The proxy is chosen in this order:

1. `--proxy URL` (or the `proxy=` argument of the [Python API](../python-api/resolve.md)).
2. `HTTPS_PROXY` or `HTTP_PROXY`.
3. `ALL_PROXY` or `SOCKS_PROXY`.

Lowercase variable names work too. SOCKS4 and SOCKS5 proxies need the optional dependency:

```bash
pip install "pip-size[socks]"
```

Without it, a SOCKS proxy is refused with a message that says what to install.

## Requests

Package lists and release metadata are fetched concurrently, up to 10 requests at a time. Each
request has a 30-second timeout. There are no automatic retries: a failure on the root package is
an error, a failure on a dependency becomes a warning.

## Cache

Every response is cached on disk for **24 hours**, so repeating a run is fast, and a run whose
data is all cached works without network access.

| System | Location |
| --- | --- |
| Linux, macOS | `$XDG_CACHE_HOME/pip-size`, or `~/.cache/pip-size` |
| Windows | `%LOCALAPPDATA%\pip-size\Cache` |

```bash
pip-size requests --no-cache      # ignore the cache for this run
pip-size --clear-cache            # delete it
```

```text
🗑  Cache cleared — 12 file(s) removed.  (/home/me/.cache/pip-size)
```

`--no-cache` neither reads nor writes the cache. Use it when you need to see a release published in
the last 24 hours.
