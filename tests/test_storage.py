import tempfile
import unittest
from pathlib import Path

from vc_agent import storage


class StorageTests(unittest.TestCase):
    def test_upsert_and_query_pipeline_items(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            old_data = storage.DATA_DIR
            old_db = storage.DB_PATH
            try:
                storage.DATA_DIR = Path(td)
                storage.DB_PATH = storage.DATA_DIR / "vc_agent.db"
                storage.init_db()
                storage.upsert_pipeline_item(
                    url="https://example.com/1",
                    topic="AI",
                    score=0.9,
                    reason="demo",
                    raw={"title": "t"},
                    summary={"one_line": "x"},
                )
                rows = storage.list_pipeline_since("1970-01-01T00:00:00+00:00")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["topic"], "AI")
            finally:
                storage.DATA_DIR = old_data
                storage.DB_PATH = old_db


if __name__ == "__main__":
    unittest.main()
