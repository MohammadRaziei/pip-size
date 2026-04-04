"""
CLI tests for pip_size.main()
Framework : unittest.TestCase
Runner    : pytest test_cli.py -v
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from pip_size.__main__ import Cache, PipSize, main


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def run_main(*args: str):
    """Run main() with given argv. Returns (exit_code, stdout, stderr)."""
    with patch("sys.argv", ["pip-size", *args]):
        try:
            main()
            return 0
        except SystemExit as e:
            return e.code or 0


class _PatchedMain(unittest.TestCase):
    """
    Base class that patches PipSize so main() never does real resolution.
    Subclasses get self.MockClass and self.instance in every test.
    """

    def setUp(self):
        self.instance  = MagicMock()
        self._patcher  = patch("pip_size.PipSize", return_value=self.instance)
        self.MockClass = self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def run_main(self, *args):
        with patch("sys.argv", ["pip-size", *args]):
            try:
                main()
            except SystemExit:
                pass

    def init_kwargs(self):
        """Return the kwargs PipSize() was constructed with."""
        _, kwargs = self.MockClass.call_args
        return kwargs


# ─────────────────────────────────────────────────────────────────────
# --clear-cache
# ─────────────────────────────────────────────────────────────────────

class TestClearCache(unittest.TestCase):

    def test_exits_zero(self):
        with patch.object(Cache, "clear", return_value=3):
            code = run_main("--clear-cache")
        self.assertEqual(code, 0)

    def test_prints_removed_count(self):
        with patch.object(Cache, "clear", return_value=7):
            with patch("builtins.print") as mock_print:
                run_main("--clear-cache")
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("7", output)


# ─────────────────────────────────────────────────────────────────────
# Missing package argument
# ─────────────────────────────────────────────────────────────────────

class TestMissingPackage(unittest.TestCase):

    def test_no_package_exits_nonzero(self):
        with patch("sys.argv", ["pip-size"]):
            with self.assertRaises(SystemExit) as cm:
                main()
        self.assertNotEqual(cm.exception.code, 0)


# ─────────────────────────────────────────────────────────────────────
# Argument wiring
# ─────────────────────────────────────────────────────────────────────

class TestArgWiring(_PatchedMain):

    def test_package_passed_to_run(self):
        self.run_main("requests")
        self.instance.run.assert_called_once_with("requests")

    def test_no_deps_flag_true(self):
        self.run_main("requests", "--no-deps")
        self.assertTrue(self.init_kwargs()["no_deps"])

    def test_no_deps_default_false(self):
        self.run_main("requests")
        self.assertFalse(self.init_kwargs()["no_deps"])

    def test_all_extras_short_flag(self):
        self.run_main("phasma", "-a")
        self.assertTrue(self.init_kwargs()["all_extras"])

    def test_all_extras_long_flag(self):
        self.run_main("phasma", "--all-extras")
        self.assertTrue(self.init_kwargs()["all_extras"])

    def test_all_extras_default_false(self):
        self.run_main("phasma")
        self.assertFalse(self.init_kwargs()["all_extras"])

    def test_extras_filter_parsed_as_set(self):
        self.run_main("phasma", "--extras", "test,dev")
        self.assertEqual(self.init_kwargs()["extras_filter"], {"test", "dev"})

    def test_extras_filter_single_value(self):
        self.run_main("requests", "--extras", "security")
        self.assertEqual(self.init_kwargs()["extras_filter"], {"security"})

    def test_extras_filter_glob(self):
        self.run_main("requests", "--extras", "dev*")
        self.assertEqual(self.init_kwargs()["extras_filter"], {"dev*"})

    def test_extras_filter_none_by_default(self):
        self.run_main("requests")
        self.assertIsNone(self.init_kwargs()["extras_filter"])

    def test_extras_whitespace_stripped(self):
        self.run_main("requests", "--extras", " test , dev ")
        self.assertEqual(self.init_kwargs()["extras_filter"], {"test", "dev"})

    def test_no_cache_flag(self):
        self.run_main("requests", "--no-cache")
        self.assertFalse(self.init_kwargs()["use_cache"])

    def test_cache_enabled_by_default(self):
        self.run_main("requests")
        self.assertTrue(self.init_kwargs()["use_cache"])

    def test_quiet_flag(self):
        self.run_main("requests", "--quiet")
        self.assertTrue(self.init_kwargs()["quiet"])

    def test_quiet_default_false(self):
        self.run_main("requests")
        self.assertFalse(self.init_kwargs()["quiet"])

    def test_bytes_flag(self):
        self.run_main("requests", "--bytes")
        self.assertTrue(self.init_kwargs()["as_bytes"])

    def test_json_flag(self):
        self.run_main("requests", "--json")
        self.assertTrue(self.init_kwargs()["as_json"])

    def test_proxy_flag(self):
        self.run_main("requests", "--proxy", "http://proxy:8080")
        self.assertEqual(self.init_kwargs()["proxy"], "http://proxy:8080")

    def test_proxy_default_none(self):
        self.run_main("requests")
        self.assertIsNone(self.init_kwargs()["proxy"])


# ─────────────────────────────────────────────────────────────────────
# Mutually exclusive flags
# ─────────────────────────────────────────────────────────────────────

class TestMutuallyExclusiveFlags(unittest.TestCase):

    def test_verbose_and_extra_verbose_conflict(self):
        with patch("sys.argv", ["pip-size", "requests", "--verbose", "--extra-verbose"]):
            with self.assertRaises(SystemExit) as cm:
                main()
        self.assertNotEqual(cm.exception.code, 0)


# ─────────────────────────────────────────────────────────────────────
# Runtime error handling
# ─────────────────────────────────────────────────────────────────────

class TestRuntimeError(unittest.TestCase):

    def test_runtime_error_exits_one(self):
        with patch("pip_size.PipSize.run", side_effect=RuntimeError("boom")):
            with patch("sys.argv", ["pip-size", "requests"]):
                with self.assertRaises(SystemExit) as cm:
                    main()
        self.assertEqual(cm.exception.code, 1)

    def test_runtime_error_message_printed(self):
        with patch("pip_size.PipSize.run", side_effect=RuntimeError("network down")):
            with patch("sys.argv", ["pip-size", "requests"]):
                with patch("builtins.print") as mock_print:
                    try:
                        main()
                    except SystemExit:
                        pass
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("network down", output)


if __name__ == "__main__":
    unittest.main()