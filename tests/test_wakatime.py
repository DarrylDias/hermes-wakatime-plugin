"""Unit tests for pure helpers. Runs with `python -m unittest discover -s tests`."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
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
        self.assertIn("hermes-wakatime/0.1.0", plugin_value)
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
        self.assertIn("hermes-wakatime/0.1.0", paths.plugin_string())


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


if __name__ == "__main__":
    unittest.main()
