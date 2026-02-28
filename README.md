# pip-size

[![PyPI - Version](https://img.shields.io/pypi/v/pip-size.svg)](https://pypi.org/project/pip-size)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pip-size.svg)](https://pypi.org/project/pip-size)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Calculate the real download size of PyPI packages and their dependencies. Zero downloads. No pip subprocess. Pure PyPI JSON API + packaging.**

`pip-size` is a lightweight Python tool that estimates the download size of Python packages from PyPI without actually downloading anything. It uses PyPI's JSON API and intelligent package resolution to provide accurate size estimates for packages and their entire dependency tree.

## ✨ Features

- **Zero Downloads**: No actual downloads occur - all data comes from PyPI's JSON API
- **Smart Resolution**: Automatically resolves dependencies recursively
- **Platform-Aware**: Selects the appropriate wheel for your platform (mimics pip's behavior)
- **Caching**: Built-in caching for faster repeated queries (24-hour TTL)
- **Multiple Output Formats**: Human-readable tree, JSON output, or quiet mode
- **Extras Support**: Handles package extras (e.g., `requests[security]`)
- **Version Pinning**: Supports exact versions and version specifiers

## 📦 Installation

```bash
pip install pip-size
```

## 🚀 Quick Start

### Basic Usage

```bash
# Check size of a package with all dependencies
pip-size requests

# Check size without dependencies
pip-size requests --no-deps

# Specify exact version
pip-size "requests==2.31.0"

# Output as JSON
pip-size requests --json

# Quiet mode (only total size)
pip-size requests --quiet
```

### Example Output

```bash
$ pip-size requests

🔍 Resolving 'requests'...
  ✓ requests==2.32.5  →  requests-2.32.5-py3-none-any.whl
    ✓ urllib3==2.3.0  →  urllib3-2.3.0-py3-none-any.whl
    ✓ charset-normalizer==3.4.1  →  charset_normalizer-3.4.1-py3-none-any.whl
    ✓ certifi==2025.1.31  →  certifi-2025.1.31-py3-none-any.whl
    ✓ idna==3.10  →  idna-3.10-py3-none-any.whl

  requests==2.32.5  (1.1 MB total)
  ├── urllib3==2.3.0  (341.8 KB)
  ├── charset-normalizer==3.4.1  (204.8 KB)
  ├── certifi==2025.1.31  (164.0 KB)
  └── idna==3.10  (61.4 KB)
```

## 📖 Usage

### Command Line Options

```
usage: pip-size [-h] [--no-deps] [--quiet] [--bytes] [--json] [--no-cache] [--clear-cache] [--verbose | --extra-verbose]
                [package]

Calculate real download size of a PyPI package. Zero downloads.

positional arguments:
  package          e.g. "requests" or "requests==2.31.0"

options:
  -h, --help       show this help message and exit
  --no-deps        Show size of the package itself only, without resolving dependencies.
  --quiet          Print only the total size and nothing else.
  --bytes          Report all sizes in raw bytes instead of human-readable units.
  --json           Output the full dependency tree as JSON.
  --no-cache       Bypass cache and always fetch fresh data from PyPI.
  --clear-cache    Delete all cached PyPI responses and exit.
  --verbose        Enable INFO logging.
  --extra-verbose  Enable DEBUG logging (HTTP requests, wheel scoring, marker evaluation, cache).
```

### Advanced Examples

```bash
# Check package with extras
pip-size "requests[security]"

# Output raw bytes
pip-size requests --bytes

# Disable caching for fresh data
pip-size requests --no-cache

# Clear the cache
pip-size --clear-cache

# Verbose output for debugging
pip-size requests --verbose
```

### JSON Output Example

```bash
$ pip-size requests --json --quiet
{
  "name": "requests",
  "version": "2.32.5",
  "size": "63.2 KB",
  "total_size": "1.1 MB",
  "filename": "requests-2.32.5-py3-none-any.whl",
  "dependencies": [
    {
      "name": "urllib3",
      "version": "2.3.0",
      "size": "341.8 KB",
      "total_size": "341.8 KB",
      "filename": "urllib3-2.3.0-py3-none-any.whl"
    },
    ...
  ]
}
```

## 🔧 How It Works

1. **API Queries**: Uses PyPI's JSON API (`https://pypi.org/pypi/{package}/json`)
2. **Version Resolution**: Resolves version specifiers using PyPI's release data
3. **Wheel Selection**: Mimics pip's wheel selection algorithm for your platform
4. **Dependency Resolution**: Recursively resolves dependencies using `requires_dist` metadata
5. **Marker Evaluation**: Evaluates environment markers and extras
6. **Caching**: Caches API responses locally (24-hour TTL)

### Platform Compatibility

`pip-size` selects the most appropriate distribution file for your platform by:
- Checking Python version compatibility (`requires_python`)
- Evaluating wheel tags against your system's supported tags
- Preferring wheels over source distributions
- Using the same priority order as pip


## 📄 License

`pip-size` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Run the test suite
6. Submit a pull request

## 📚 Related Projects

- [pip](https://pip.pypa.io/) - The Python package installer
- [pip-api](https://pypi.org/project/pip-api/) - Unofficial pip API
- [pypi-simple](https://pypi.org/project/pypi-simple/) - PyPI simple API client

## 🙏 Acknowledgments

- PyPI for providing the JSON API
- The `packaging` library for version and requirement parsing
- All contributors and users of the project

---

**Note**: `pip-size` provides estimates based on PyPI metadata. Actual download sizes may vary slightly due to compression, network overhead, or other factors.