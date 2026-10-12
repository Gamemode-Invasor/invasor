import os
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

    def test_a_store_whose_owner_is_gone_refuses_to_write(self):
        from invasor.schema import Unavailable
        alive = [True]
        store = JsonStore(self.path, lambda: alive[0])
        store.save({"a": 1})
        alive[0] = False
        with self.assertRaises(Unavailable):
            store.save({"a": 2})
        self.assertEqual(store.load(), {"a": 1})

    def test_the_real_error_is_not_hidden_by_the_cleanup(self):
        from unittest import mock

        real = os.replace

        def fail_after_rename(a, b):
            real(a, b)
            raise OSError("late failure")

        with mock.patch("invasor.storage.os.replace", fail_after_rename), self.assertRaisesRegex(OSError, "late failure"):
            self.store.save({"a": 1})

    def test_locks_are_not_kept_for_stores_nobody_uses(self):
        import gc
        from invasor import storage
        JsonStore(self.path.parent / "games" / "123.json")
        gc.collect()
        self.assertNotIn(str((self.path.parent / "games" / "123.json").resolve()), storage._locks)

    def test_data_is_flushed_to_disk_before_the_rename(self):
        from unittest import mock
        order = []
        real_replace = os.replace
        with mock.patch("invasor.storage.os.fsync", lambda fd: order.append("fsync")), \
                mock.patch("invasor.storage.os.replace", lambda a, b: (order.append("replace"), real_replace(a, b))):
            self.store.save({"a": 1})
        self.assertEqual(order, ["fsync", "replace", "fsync"])  # file, rename, then the folder
        self.assertEqual(self.store.load(), {"a": 1})

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
