import unittest
from unittest import mock

from invasor import cef_flag

from .helpers import patch, temp_dir


class Flag(unittest.TestCase):
    def setUp(self):
        root = temp_dir(self)
        self.home = root / "home"
        self.data = root / "data" / "invasor"
        self.steam = self.home / ".steam/steam"
        patch(self, "invasor.cef_flag.HOME", self.home)
        patch(self, "invasor.config.DATA_DIR", self.data)
        self.flag = self.steam / ".cef-enable-remote-debugging"
        self.marker = self.data / ".cef-flag-created"

    def test_steam_dir_order(self):
        self.assertIsNone(cef_flag.steam_dir())
        fallback = self.home / ".local/share/Steam"
        fallback.mkdir(parents=True)
        self.assertEqual(cef_flag.steam_dir(), fallback)
        self.steam.mkdir(parents=True)
        self.assertEqual(cef_flag.steam_dir(), self.steam)  # ~/.steam/steam first, like the installer

    def test_a_missing_flag_is_created_with_its_marker(self):
        self.steam.mkdir(parents=True)
        with self.assertLogs("invasor.cef_flag", "INFO") as logs:
            self.assertIs(cef_flag.ensure(), True)
        self.assertTrue(self.flag.exists())
        self.assertTrue(self.marker.exists())  # so --uninstall removes it, like the installer's own
        self.assertIn("recreated", logs.output[0])
        self.assertIs(cef_flag.ensure(), False)  # now there: nothing more to do

    def test_an_existing_flag_is_left_alone(self):
        self.steam.mkdir(parents=True)
        self.flag.write_text("someone else's")
        self.assertIs(cef_flag.ensure(), False)
        self.assertEqual(self.flag.read_text(), "someone else's")
        self.assertFalse(self.marker.exists())  # not ours: uninstall must not remove it

    def test_a_link_counts_as_present(self):
        self.steam.mkdir(parents=True)
        self.flag.symlink_to(self.steam / "nowhere")  # dangling
        self.assertIs(cef_flag.ensure(), False)
        self.assertFalse(self.marker.exists())

    def test_no_steam_folder_does_nothing(self):
        self.assertIs(cef_flag.ensure(), False)
        self.assertFalse(self.data.exists())

    def test_it_never_raises(self):
        self.steam.mkdir(parents=True)
        with mock.patch("pathlib.Path.touch", side_effect=OSError("read-only")), \
                self.assertLogs("invasor.cef_flag", "WARNING") as logs:
            self.assertIs(cef_flag.ensure(), False)
        self.assertIn("couldn't create", logs.output[0])
        self.assertFalse(self.flag.exists())

    def test_a_marker_that_cant_be_written_keeps_the_flag(self):
        self.steam.mkdir(parents=True)
        real = cef_flag.Path.touch

        def touch(path, *a, **k):
            if path.name == cef_flag.MARKER_NAME:
                raise OSError("no space")
            return real(path, *a, **k)

        with mock.patch("pathlib.Path.touch", touch), self.assertLogs("invasor.cef_flag", "WARNING") as logs:
            self.assertIs(cef_flag.ensure(), True)
        self.assertTrue(self.flag.exists())
        self.assertIn("not its marker", logs.output[0])
