'''
测试偏好学习的持久化与重算
'''
import json
import tempfile
import unittest
from pathlib import Path

from vc_agent import preferences


class PreferencesTests(unittest.TestCase):
    def test_append_feedback_rebuilds_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            old_data = preferences.DATA_DIR
            old_feedback = preferences.FEEDBACK_PATH
            old_prefs = preferences.PREFS_PATH
            try:
                preferences.DATA_DIR = data_dir
                preferences.FEEDBACK_PATH = data_dir / "feedback.jsonl"
                preferences.PREFS_PATH = data_dir / "preferences.json"
                preferences.append_feedback("https://example.com/1", "up", source="YouTube", author="alice")
                prefs = json.loads(preferences.PREFS_PATH.read_text(encoding="utf-8"))
                self.assertIn("YouTube", prefs["sources"])
                self.assertIn("alice", prefs["authors"])
            finally:
                preferences.DATA_DIR = old_data
                preferences.FEEDBACK_PATH = old_feedback
                preferences.PREFS_PATH = old_prefs


if __name__ == "__main__":
    unittest.main()
