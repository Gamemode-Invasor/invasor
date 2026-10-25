import os
import unittest

from invasor import paths
from invasor.schema import InvalidArgument, Unavailable

from .helpers import patch, temp_dir

STEAMID_BASE = 76561197960265728


def real(items):
    return [os.path.realpath(p) for p in items]


def library_folders(*folders):
    body = "".join(f'\t"{i}"\n\t{{\n\t\t"{key}"\t\t"{path}"\n\t}}\n' for i, (key, path) in enumerate(folders))
    return f'"libraryfolders"\n{{\n{body}}}\n'


def login_users(*users):
    """users: (steamid64, {key: value})."""
    body = ""
    for steamid, fields in users:
        inner = "".join(f'\t\t"{k}"\t\t"{v}"\n' for k, v in fields.items())
        body += f'\t"{steamid}"\n\t{{\n{inner}\t}}\n'
    return f'"users"\n{{\n{body}}}\n'


class SteamCase(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.root = self.tmp / "Steam"
        (self.root / "steamapps").mkdir(parents=True)
        self.media = self.tmp / "media"
        self.media.mkdir()
        patch(self, "invasor.paths.STEAM_ROOT", self.root)
        patch(self, "invasor.paths.MEDIA_GLOBS", (str(self.media / "*" / "steamapps"), str(self.media / "*" / "*" / "steamapps")))

    def library(self, name):
        lib = self.tmp / name
        (lib / "steamapps").mkdir(parents=True)
        return lib

    def write_libraries(self, *folders):
        (self.root / "steamapps" / "libraryfolders.vdf").write_text(library_folders(*folders))

    def write_users(self, *users):
        f = self.root / "config" / "loginusers.vdf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(login_users(*users))

    def account_dirs(self, *names):
        for name in names:
            (self.root / "userdata" / name).mkdir(parents=True)


class SteamRoot(SteamCase):
    def test_override_wins(self):
        self.assertEqual(paths.steam_root(), self.root)
        self.assertEqual(paths.root(), self.root)

    def test_none_found_is_unavailable(self):
        patch(self, "invasor.paths.STEAM_ROOT", None)
        patch(self, "invasor.cef_flag.HOME", self.tmp / "nohome")
        self.assertIsNone(paths.root())
        with self.assertRaises(Unavailable):
            paths.steam_root()

    def test_falls_back_to_installer_lookup(self):
        home = self.tmp / "home"
        (home / ".local/share/Steam").mkdir(parents=True)
        patch(self, "invasor.paths.STEAM_ROOT", None)
        patch(self, "invasor.cef_flag.HOME", home)
        self.assertEqual(real([paths.steam_root()]), real([home / ".local/share/Steam"]))


class Libraries(SteamCase):
    def test_main_only_without_libraryfolders(self):
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps"]))

    def test_main_first_then_libraryfolders_in_order(self):
        a, b = self.library("LibA"), self.library("LibB")
        self.write_libraries(("path", self.root), ("path", b), ("path", a))
        libs = paths.libraries()
        self.assertTrue(all(isinstance(p, os.PathLike) for p in libs))
        self.assertEqual(real(libs), real([self.root / "steamapps", b / "steamapps", a / "steamapps"]))

    def test_path_key_case_is_ignored(self):
        a = self.library("LibA")
        self.write_libraries(("Path", a))
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps", a / "steamapps"]))

    def test_missing_paths_are_dropped(self):
        a = self.library("LibA")
        gone = self.tmp / "Gone"
        no_steamapps = self.tmp / "Empty"
        no_steamapps.mkdir()
        self.write_libraries(("path", gone), ("path", no_steamapps), ("path", a))
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps", a / "steamapps"]))

    def test_unreadable_libraryfolders_is_skipped(self):
        # A directory where the file should be: open() raises OSError (IsADirectoryError).
        (self.root / "steamapps" / "libraryfolders.vdf").mkdir()
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps"]))

    def test_garbage_libraryfolders_is_harmless(self):
        (self.root / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders" { "0" "notadict" } } }')
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps"]))

    def test_dedupe_by_realpath(self):
        a = self.library("LibA")
        link = self.tmp / "LinkToA"
        link.symlink_to(a)
        root_link = self.tmp / "LinkToRoot"
        root_link.symlink_to(self.root)
        self.write_libraries(("path", self.root), ("path", root_link), ("path", a), ("path", link), ("path", a))
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps", a / "steamapps"]))

    def test_mounted_media_counted_after_libraryfolders(self):
        a = self.library("LibA")
        self.write_libraries(("path", a))
        (self.media / "SD" / "steamapps").mkdir(parents=True)
        (self.media / "deck" / "USB" / "steamapps").mkdir(parents=True)
        (self.media / "nosteam").mkdir()
        libs = real(paths.libraries())
        self.assertEqual(libs[:2], real([self.root / "steamapps", a / "steamapps"]))
        self.assertEqual(sorted(libs[2:]), sorted(real([self.media / "SD" / "steamapps", self.media / "deck" / "USB" / "steamapps"])))

    def test_media_already_listed_is_not_repeated(self):
        sd = self.media / "SD"
        (sd / "steamapps").mkdir(parents=True)
        self.write_libraries(("path", sd))
        self.assertEqual(real(paths.libraries()), real([self.root / "steamapps", sd / "steamapps"]))

    def test_no_steam_is_unavailable(self):
        patch(self, "invasor.paths.STEAM_ROOT", None)
        patch(self, "invasor.cef_flag.HOME", self.tmp / "nohome")
        with self.assertRaises(Unavailable):
            paths.libraries()


class Account(SteamCase):
    def test_real_example_from_the_deck(self):
        # Only one user, lowercase "timestamp", no MostRecent; userdata also has "0".
        self.account_dirs("0", "116437234")
        self.write_users(("76561198076702962", {"AccountName": "x", "AutoLogin": "1", "timestamp": "1792921146"}))
        self.assertEqual(paths.userdata(), self.root / "userdata" / "116437234")

    def test_steamid64_conversion(self):
        self.assertEqual(76561198076702962 - STEAMID_BASE, 116437234)

    def test_most_recent_wins_over_timestamp(self):
        self.account_dirs("1", "2")
        self.write_users(
            (str(STEAMID_BASE + 1), {"MostRecent": "1", "Timestamp": "100"}),
            (str(STEAMID_BASE + 2), {"MostRecent": "0", "Timestamp": "999"}),
        )
        self.assertEqual(paths.userdata(), self.root / "userdata" / "1")

    def test_highest_timestamp_without_most_recent(self):
        self.account_dirs("1", "2", "3")
        self.write_users(
            (str(STEAMID_BASE + 1), {"timestamp": "500"}),
            (str(STEAMID_BASE + 2), {"timestamp": "9000"}),
            (str(STEAMID_BASE + 3), {"timestamp": "800"}),
        )
        self.assertEqual(paths.userdata(), self.root / "userdata" / "2")

    def test_timestamp_compared_as_integer(self):
        # "900" > "1000" as strings; as integers 1000 wins.
        self.account_dirs("1", "2")
        self.write_users(
            (str(STEAMID_BASE + 1), {"timestamp": "900"}),
            (str(STEAMID_BASE + 2), {"timestamp": "1000"}),
        )
        self.assertEqual(paths.userdata(), self.root / "userdata" / "2")

    def test_only_one_userdata_folder_without_loginusers(self):
        self.account_dirs("0", "ac", "notes", "4242")
        self.assertEqual(paths.userdata(), self.root / "userdata" / "4242")

    def test_zero_ac_and_non_digit_folders_are_never_accounts(self):
        self.account_dirs("0", "ac", "abc")
        with self.assertRaises(Unavailable):
            paths.userdata()

    def test_non_digit_steamid_is_skipped(self):
        self.account_dirs("5", "6")
        self.write_users(
            ("anonymous", {"MostRecent": "1", "timestamp": "99999"}),
            (str(STEAMID_BASE + 6), {"timestamp": "10"}),
        )
        self.assertEqual(paths.userdata(), self.root / "userdata" / "6")

    def test_chosen_account_without_folder_falls_through_to_single_folder(self):
        self.account_dirs("0", "7")
        self.write_users((str(STEAMID_BASE + 99), {"MostRecent": "1", "timestamp": "1"}))
        self.assertEqual(paths.userdata(), self.root / "userdata" / "7")

    def test_chosen_account_without_folder_and_several_folders_is_unavailable(self):
        self.account_dirs("7", "8")
        self.write_users((str(STEAMID_BASE + 99), {"MostRecent": "1"}))
        with self.assertRaises(Unavailable):
            paths.userdata()

    def test_ambiguous_is_unavailable(self):
        self.account_dirs("7", "8")
        with self.assertRaises(Unavailable):
            paths.userdata()

    def test_no_userdata_is_unavailable(self):
        with self.assertRaises(Unavailable):
            paths.userdata()

    def test_garbage_loginusers_falls_through(self):
        self.account_dirs("7")
        f = self.root / "config" / "loginusers.vdf"
        f.parent.mkdir(parents=True)
        f.write_text('"users" { "x" } } "{')
        self.assertEqual(paths.userdata(), self.root / "userdata" / "7")

    def test_all_accounts_sorted(self):
        self.account_dirs("300", "0", "ac", "20", "abc", "1000")
        found = paths.userdata(all=True)
        self.assertEqual([p.name for p in found], ["20", "300", "1000"])  # numeric order
        self.assertTrue(all(p.parent == self.root / "userdata" for p in found))

    def test_all_accounts_empty(self):
        self.assertEqual(paths.userdata(all=True), [])
        self.account_dirs("0", "ac")
        self.assertEqual(paths.userdata(all=True), [])

    def test_grid_dir(self):
        self.account_dirs("4242")
        self.assertEqual(paths.grid_dir(), self.root / "userdata" / "4242" / "config" / "grid")

    def test_grid_dir_unavailable_without_account(self):
        with self.assertRaises(Unavailable):
            paths.grid_dir()


class AppFolders(SteamCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.library("LibA"), self.library("LibB")
        self.write_libraries(("path", self.root), ("path", self.a), ("path", self.b))

    def check(self, func, kind):
        # main library fallback
        self.assertEqual(real([func(570)]), real([self.root / "steamapps" / kind / "570"]))
        # library holding the appmanifest
        (self.b / "steamapps" / "appmanifest_570.acf").write_text('"AppState" { "appid" "570" }')
        self.assertEqual(real([func(570)]), real([self.b / "steamapps" / kind / "570"]))
        # an existing folder wins over the appmanifest
        (self.a / "steamapps" / kind / "570").mkdir(parents=True)
        self.assertEqual(real([func(570)]), real([self.a / "steamapps" / kind / "570"]))
        self.assertEqual(real([func("570")]), real([self.a / "steamapps" / kind / "570"]))

    def test_compatdata(self):
        self.check(paths.compatdata, "compatdata")

    def test_shadercache(self):
        self.check(paths.shadercache, "shadercache")

    def test_existing_folder_in_second_library_wins_over_main(self):
        (self.b / "steamapps" / "compatdata" / "10").mkdir(parents=True)
        self.assertEqual(real([paths.compatdata("10")]), real([self.b / "steamapps" / "compatdata" / "10"]))

    def test_a_file_named_like_the_folder_does_not_count(self):
        (self.a / "steamapps" / "compatdata").mkdir()
        (self.a / "steamapps" / "compatdata" / "10").write_text("")
        self.assertEqual(real([paths.compatdata(10)]), real([self.root / "steamapps" / "compatdata" / "10"]))

    def test_limits_accepted(self):
        self.assertEqual(paths.compatdata(1).name, "1")
        self.assertEqual(paths.shadercache(2**32 - 1).name, str(2**32 - 1))

    def test_bad_appids(self):
        for bad in (True, False, -1, 0, 2**32, "12a", "", "-5", " 12", 1.0, None, [570]):
            for func in (paths.compatdata, paths.shadercache):
                with self.subTest(func=func.__name__, appid=bad):
                    with self.assertRaises(InvalidArgument):
                        func(bad)


if __name__ == "__main__":
    unittest.main()
