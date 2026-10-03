import unittest

from invasor.storage import JsonStore

from .helpers import temp_dir


class Store(unittest.TestCase):
    def setUp(self):
        self.path = temp_dir(self) / "sub" / "s.json"
        self.store = JsonStore(self.path)

    def test_missing_is_empty_and_save_creates_dirs(self):
        self.assertEqual(self.store.load(), {})
        self.store.save({"a": 1, "ñ": "sí"})
        self.assertEqual(self.store.load(), {"a": 1, "ñ": "sí"})

    def test_update_merges(self):
        self.store.save({"a": 1})
        self.assertEqual(self.store.update(b=2), {"a": 1, "b": 2})

    def test_corrupt_or_non_object_reads_as_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        with self.assertLogs("invasor.storage", "ERROR"):
            self.assertEqual(self.store.load(), {})
        # Moved aside once (kept for diagnosis): the next read is quiet.
        self.assertTrue(self.path.with_name("s.json.corrupt").exists())
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.load(), {})
        self.path.write_text("[1, 2]")
        self.assertEqual(self.store.load(), {})

    def test_transform_saves_only_changes(self):
        self.store.save({"a": 1})
        mtime = self.path.stat().st_mtime_ns
        self.assertEqual(self.store.transform(lambda d: d), {"a": 1})
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.assertEqual(self.store.transform(lambda d: {**d, "b": 2}), {"a": 1, "b": 2})
        self.assertEqual(self.store.load(), {"a": 1, "b": 2})

    def test_no_temp_files_left_behind(self):
        self.store.save({"a": 1})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["s.json"])


if __name__ == "__main__":
    unittest.main()
