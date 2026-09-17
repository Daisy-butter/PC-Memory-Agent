import tempfile
import unittest
from pathlib import Path

from pcmemory.indexer import Indexer, sync_registered
from pcmemory.store import Store


class MemoryTests(unittest.TestCase):
    def test_index_search_and_rename_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "客户A合同.txt"
            document.write_text("客户A 2026 年合同。最终报价为 100 万元。", encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                self.assertEqual(Indexer(store).sync(root)["indexed"], 1)
                results = store.search("客户A 合同")
                self.assertEqual(len(results), 1)
                document.rename(root / "客户A最终合同.txt")
                Indexer(store).sync(root)
                self.assertEqual(store.get_file(results[0]["id"])["name"], "客户A最终合同.txt")
                self.assertIn("客户A合同.txt", store.conn.execute("SELECT previous_path FROM file_history").fetchone()[0])
            finally:
                store.close()

    def test_registered_roots_refresh_before_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "notes.txt"
            document.write_text("旧内容", encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                Indexer(store).sync(root)
                document.write_text("最新项目计划", encoding="utf-8")
                report = sync_registered(store)
                self.assertIn(str(root.resolve()), report)
                self.assertEqual(len(store.search("最新项目")), 1)
                document.unlink()
                sync_registered(store)
                self.assertEqual(store.search("最新项目"), [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()