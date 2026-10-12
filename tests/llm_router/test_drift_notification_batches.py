"""Tests for bounded grouping of sanitized router drift findings."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[2] / "roles/llm_router/filter_plugins/drift_notifications.py"
SPEC = spec_from_file_location("drift_notifications", PLUGIN.resolve())
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _items(result):
    return [
        (message["git_file"], item[2:])
        for message in result["messages"]
        for item in message["text"].splitlines()[1:]
    ]


class DriftNotificationBatchTests(unittest.TestCase):
    def test_zero_findings_are_silent(self):
        self.assertEqual(
            MODULE.drift_notification_batches([]), {"messages": [], "oversized": []}
        )

    def test_groups_files_sorts_stably_and_preserves_duplicates(self):
        findings = [
            {"git_file": "b.yml", "item": "setting z"},
            {"git_file": "a.yml", "item": "setting c"},
            {"git_file": "a.yml", "item": "setting a"},
            {"git_file": "a.yml", "item": "setting a"},
        ]
        first = MODULE.drift_notification_batches(findings)
        second = MODULE.drift_notification_batches(list(reversed(findings)))
        self.assertEqual(first, second)
        self.assertEqual(
            _items(first),
            [
                ("a.yml", "setting a"),
                ("a.yml", "setting a"),
                ("a.yml", "setting c"),
                ("b.yml", "setting z"),
            ],
        )

    def test_many_unicode_items_are_whole_and_fit_the_utf8_limit(self):
        findings = [
            {"git_file": "配置/routers.yml", "item": f"model-設定-{index:03d}-🙂"}
            for index in range(250)
        ]
        result = MODULE.drift_notification_batches(findings, max_bytes=700)
        self.assertGreater(len(result["messages"]), 1)
        self.assertFalse(result["oversized"])
        for message in result["messages"]:
            self.assertLessEqual(len(message["text"].encode("utf-8")), 700)
            self.assertIn("part ", message["text"].splitlines()[0])
        self.assertEqual(
            _items(result),
            [("配置/routers.yml", f"model-設定-{i:03d}-🙂") for i in range(250)],
        )

    def test_single_item_over_limit_is_reported_for_visible_failure(self):
        result = MODULE.drift_notification_batches(
            [{"git_file": "routers.yml", "item": "x" * 100}], max_bytes=80
        )
        self.assertEqual(result["messages"], [])
        self.assertEqual(
            result["oversized"], [{"git_file": "routers.yml", "item": "x" * 100}]
        )

    def test_unicode_byte_limit_not_character_limit(self):
        result = MODULE.drift_notification_batches(
            [{"git_file": "a.yml", "item": "🙂" * 35}], max_bytes=100
        )
        self.assertTrue(result["oversized"])


if __name__ == "__main__":
    unittest.main()
