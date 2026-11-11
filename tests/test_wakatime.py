"""Unit tests for pure helpers. Runs with `python -m unittest discover -s tests`."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PACKAGE = "hermes_wakatime_plugin"
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)

from hermes_wakatime_plugin import wakatime_client as client  # noqa: E402
from hermes_wakatime_plugin import wakatime_install as installer  # noqa: E402
from hermes_wakatime_plugin import wakatime_paths as paths  # noqa: E402
from hermes_wakatime_plugin import wakatime_tools as tools  # noqa: E402


class LineDeltaTests(unittest.TestCase):
    def test_replace_and_insert(self):
        old = "alpha\nbeta\ngamma"
        new = "alpha\nbeta\ngamma\ndelta"
        self.assertEqual(tools._line_delta(old, new), 1)

    def test_deletion_is_negative(self):
        old = "a\nb\nc"
        new = "a\nc"
        self.assertEqual(tools._line_delta(old, new), -1)

    def test_replace_counts_add_and_remove(self):
        old = "a\nb"
        new = "a\nx"
        self.assertEqual(tools._line_delta(old, new), 0)

    def test_empty_strings(self):
        self.assertEqual(tools._line_delta("", ""), 0)


class HeartbeatArgsTests(unittest.TestCase):
    def test_file_entity_is_made_absolute_and_flags_present(self):
        args = client.build_heartbeat_args(
            "relative/test.py",
            category="ai coding",
            alternate_project="hermes",
            is_write=True,
            ai_line_changes=-2,
            sync_limit=1,
        )
        self.assertIn("--entity", args)
        entity = args[args.index("--entity") + 1]
        self.assertTrue(os.path.isabs(entity))
        self.assertIn("--plugin", args)
        plugin_value = args[args.index("--plugin") + 1]
        self.assertIn("hermes-wakatime/0.2.0", plugin_value)
        self.assertIn("--write", args)
        self.assertIn("--ai-line-changes", args)
        self.assertIn("--sync-ai-disabled", args)
        self.assertEqual(args[args.index("--sync-offline-activity") + 1], "1")

    def test_project_override_wins_over_alternate(self):
        args = client.build_heartbeat_args(
            "x.zig", project="proj", alternate_project="fallback"
        )
        self.assertIn("--project", args)
        self.assertNotIn("--alternate-project", args)

    def test_domain_entity_is_not_path_resolved(self):
        args = client.build_heartbeat_args("wakatime.com", entity_type="domain")
        self.assertEqual(args[args.index("--entity") + 1], "wakatime.com")

    def test_sync_limit_zero_is_clamped_not_negative(self):
        args = client.build_heartbeat_args("x.zig", sync_limit=-5)
        self.assertEqual(args[args.index("--sync-offline-activity") + 1], "0")


class SummaryTests(unittest.TestCase):
    def test_summarize_today(self):
        data = {
            "grand_total": {"total_seconds": 3725.5, "text": "1 hr 2 mins", "ai_additions": 12, "ai_deletions": 3},
            "range": {"date": "2026-11-11", "text": "Wed Nov 11th 2026", "timezone": "Asia/Kolkata"},
            "categories": [{"name": "AI Coding", "text": "58 mins", "total_seconds": 3485.5, "percent": 93.5}],
            "projects": [{"name": "ozone", "text": "40 mins", "total_seconds": 2400}],
            "languages": [{"name": "PHP", "text": "44 mins", "total_seconds": 2640}],
            "editors": [{"name": "Hermes", "text": "1 hr 2 mins", "total_seconds": 3725}],
        }
        summary = client.summarize_today(data)
        self.assertEqual(summary["time_text"], "1 hr 2 mins")
        self.assertEqual(summary["account_timezone"], "Asia/Kolkata")
        self.assertEqual(summary["ai_additions"], 12)
        self.assertEqual(summary["ai_deletions"], 3)
        self.assertEqual(summary["projects"][0]["name"], "ozone")
        self.assertEqual(len(summary["categories"]), 1)

    def test_summarize_stats(self):
        data = {
            "total_seconds": 7261,
            "daily_average": 3600,
            "is_up_to_date": True,
            "best_day": {"date": "2026-11-09", "text": "1 hr", "total_seconds": 3600},
            "categories": [{"name": "AI Coding", "text": "1 hr", "total_seconds": 3600}],
            "projects": [],
            "languages": [{"name": "Python", "text": "30 mins", "total_seconds": 1800}],
            "editors": [],
        }
        summary = client.summarize_stats(data)
        self.assertEqual(summary["time_text"], "2 hr 1 min")
        self.assertEqual(summary["daily_average_text"], "1 hr")
        self.assertEqual(summary["best_day"]["total_seconds"], 3600)

    def test_duration_formatting(self):
        self.assertEqual(client.summarize_duration_seconds(0), "0 secs")
        self.assertEqual(client.summarize_duration_seconds(61), "1 min")
        self.assertEqual(client.summarize_duration_seconds(3661), "1 hr 1 min")


class CredentialMaskingTests(unittest.TestCase):
    def test_mask_is_not_reversible(self):
        masked = paths.mask_key("waka_0123-4567-89ab-cdef-0123456789ab")
        self.assertNotIn("waka_0123", masked)
        self.assertTrue(masked.startswith("configured"))
        self.assertEqual(paths.mask_key(None), "not configured")

    def test_plugin_string(self):
        self.assertTrue(paths.plugin_string().startswith("hermes/"))
        self.assertIn("hermes-wakatime/0.2.0", paths.plugin_string())


class ToolSuccessTests(unittest.TestCase):
    def test_json_errors_and_success(self):
        self.assertFalse(tools._tool_succeeded(json.dumps({"success": False})))
        self.assertFalse(tools._tool_succeeded(json.dumps({"error": "boom"})))
        self.assertTrue(tools._tool_succeeded(json.dumps({"success": True})))
        self.assertTrue(tools._tool_succeeded("--- a/file\n+++ b/file\n"))

    def test_path_extraction(self):
        self.assertEqual(tools._path_from_args({"path": "src/x.py"}), "src/x.py")
        self.assertEqual(tools._path_from_args({"file_path": "a.py"}), "a.py")
        self.assertIsNone(tools._path_from_args({"command": "ls"}))


class InstallerTests(unittest.TestCase):
    def test_platform_assets(self):
        cases = [
            ("Windows", "x86_64", "wakatime-cli-windows-amd64.zip"),
            ("Windows", "arm64", "wakatime-cli-windows-arm64.zip"),
            ("Linux", "x86_64", "wakatime-cli-linux-amd64.zip"),
            ("Linux", "aarch64", "wakatime-cli-linux-arm64.zip"),
            ("Linux", "riscv64", "wakatime-cli-linux-riscv64.zip"),
            ("Darwin", "arm64", "wakatime-cli-darwin-arm64.zip"),
            ("FreeBSD", "amd64", "wakatime-cli-freebsd-amd64.zip"),
        ]
        for system, machine, expected in cases:
            self.assertEqual(installer.asset_name(system, machine), expected, (system, machine))

    def test_unsupported_and_missing_platform(self):
        with self.assertRaises(installer.InstallerError):
            installer.asset_name("Solaris", "sparc")
        with self.assertRaises(installer.InstallerError):
            installer.asset_name("Darwin", "i386")
        with self.assertRaises(installer.InstallerError):
            installer.asset_name("Windows", "arm")

    def test_executable_name(self):
        self.assertEqual(installer.executable_name("Windows"), "wakatime-cli.exe")
        self.assertEqual(installer.executable_name("Linux"), "wakatime-cli")

    def test_version_normalization(self):
        self.assertIsNone(installer._normalize_version("latest"))
        self.assertIsNone(installer._normalize_version(None))
        self.assertEqual(installer._normalize_version("v2.28.4"), "2.28.4")
        self.assertEqual(installer._normalize_version("2.28.4"), "2.28.4")
        with self.assertRaises(installer.InstallerError):
            installer._normalize_version("develop")

    def test_checksum_parser(self):
        body = b"7601aa...\nnot-an-asset.zip\nabc"
        valid = b"76f90160dd5268f3b74be2aa75323c9387e79db0874cae71269d1d74c88dd661 wakatime-cli-windows-amd64.zip\n"
        with unittest.mock.patch.object(installer, "_request", return_value=valid):
            self.assertEqual(
                installer.expected_sha256("v2.28.4", "wakatime-cli-windows-amd64.zip"),
                "76f90160dd5268f3b74be2aa75323c9387e79db0874cae71269d1d74c88dd661",
            )
        with unittest.mock.patch.object(installer, "_request", return_value=body), self.assertRaises(installer.InstallerError):
            installer.expected_sha256("v2.28.4", "wakatime-cli-windows-amd64.zip")

    def test_extract_executable_from_release_style_zip(self):
        import io
        import zipfile

        for entry in ("wakatime-cli-linux-amd64", "wakatime-cli-windows-amd64.exe"):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as zf:
                zf.writestr(entry, b"binary-content")
            self.assertEqual(installer._extract_executable(data.getvalue(), "wakatime-cli"), b"binary-content")
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as zf:
            zf.writestr("notes.txt", b"no executable")
        with self.assertRaises(installer.InstallerError):
            installer._extract_executable(data.getvalue(), "wakatime-cli")


class VersionCompareTests(unittest.TestCase):
    def test_installed_version_matching(self):
        from hermes_wakatime_plugin import wakatime_cli

        self.assertTrue(wakatime_cli._version_matches("v2.28.4", "v2.28.4"))
        self.assertTrue(wakatime_cli._version_matches("wakatime-cli v2.28.4", "v2.28.4"))
        self.assertFalse(wakatime_cli._version_matches("v2.27.0", "v2.28.4"))
        self.assertFalse(wakatime_cli._version_matches(None, "v2.28.4"))


if __name__ == "__main__":
    unittest.main()
