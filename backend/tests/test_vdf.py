import struct
import unittest

from invasor import context, vdf

from .helpers import patch, temp_dir


def shortcut(appid, name, name_key="AppName", exe=None):
    return (
        b"\x02appid\x00" + struct.pack("<i", appid)
        + b"\x01" + name_key.encode() + b"\x00" + name.encode() + b"\x00"
        + (b"\x01Exe\x00" + exe.encode() + b"\x00" if exe is not None else b"")
        + b"\x08"
    )


def shortcuts_file(*entries):
    body = b"".join(b"\x00" + str(i).encode() + b"\x00" + e for i, e in enumerate(entries))
    return b"\x00shortcuts\x00" + body + b"\x08\x08"


class BinaryVDF(unittest.TestCase):
    def test_parses_nested_maps_and_types(self):
        data = shortcuts_file(shortcut(123, "Juego"))
        self.assertEqual(vdf.loads_binary(data), {"shortcuts": {"0": {"appid": 123, "AppName": "Juego"}}})

    def test_garbage_raises_vdf_error(self):
        with self.assertRaises(vdf.VDFError):
            vdf.loads_binary(b"\x09nope\x00")
        with self.assertRaises(vdf.VDFError):
            vdf.loads_binary(b"\x01unterminated")


class ShortcutNames(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        patch(self, "invasor.paths.STEAM_ROOT", self.root)
        patch(self, "invasor.context._shortcuts_cache", {"key": None, "names": {}, "exes": {}})

    def write(self, user, data):
        f = self.root / "userdata" / user / "config" / "shortcuts.vdf"
        f.parent.mkdir(parents=True)
        f.write_bytes(data)

    def test_signed_appids_become_unsigned_and_key_case_is_ignored(self):
        # 4000000000 is stored as a negative int32 (-294967296), as Steam does.
        self.write("111", shortcuts_file(shortcut(-294967296, "My Shortcut"), shortcut(42, "Otro", "appname")))
        self.assertEqual(context._shortcut_names(), {"4000000000": "My Shortcut", "42": "Otro"})

    def test_corrupt_file_is_skipped(self):
        self.write("111", b"\x09broken")
        self.write("222", shortcuts_file(shortcut(7, "Bueno")))
        with self.assertLogs("invasor.context", "WARNING"):
            self.assertEqual(context._shortcut_names(), {"7": "Bueno"})


    def test_shortcut_executables(self):
        self.write("111", shortcuts_file(
            shortcut(42, "Quoted", exe='"/games/My Game/Game.exe"'),
            shortcut(43, "Plain", exe="/usr/bin/emulator"),
            shortcut(44, "None"),
        ))
        game = context.GameContext()
        self.assertEqual(game.shortcut_exe(42), "/games/My Game/Game.exe")
        self.assertEqual(game.shortcut_exe("43"), "/usr/bin/emulator")
        self.assertIsNone(game.shortcut_exe(44))
        self.assertEqual(game.info(42)["shortcut"], True)


class TextVDF(unittest.TestCase):
    def test_nested_blocks(self):
        text = '"AppState"\n{\n\t"appid"\t\t"570"\n\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n}\n'
        self.assertEqual(vdf.loads_text(text), {"AppState": {"appid": "570", "UserConfig": {"language": "english"}}})

    def test_repeated_key_keeps_last(self):
        self.assertEqual(vdf.loads_text('"a" "1" "a" "2" "b" { "x" "1" } "b" { "y" "2" }'), {"a": "2", "b": {"y": "2"}})

    def test_unnamed_block_is_skipped(self):
        self.assertEqual(vdf.loads_text('"a" "1" { "lost" "x" } "b" "2"'), {"a": "1", "b": "2"})

    def test_escapes(self):
        self.assertEqual(vdf.loads_text(r'"k" "a\nb\tc\\d\"e"'), {"k": 'a\nb\tc\\d"e'})
        self.assertEqual(vdf.loads_text(r'"C:\\Games" "1"'), {"C:\\Games": "1"})

    def test_unbalanced_braces_do_not_raise(self):
        self.assertEqual(vdf.loads_text('} "a" "1" } } "b" "2"'), {"a": "1", "b": "2"})
        self.assertEqual(vdf.loads_text('"a" { "b" { "c" "1"'), {"a": {"b": {"c": "1"}}})
        self.assertEqual(vdf.loads_text(""), {})

    def test_non_str_raises_vdf_error(self):
        for bad in (b'"a" "1"', None, 1, {"a": "1"}):
            with self.subTest(value=bad), self.assertRaises(vdf.VDFError):
                vdf.loads_text(bad)

    def test_vdf_error_is_invalid_argument(self):
        from invasor import schema
        self.assertTrue(issubclass(vdf.VDFError, schema.InvalidArgument))
        self.assertIsInstance(vdf.VDFError("x"), schema.InvalidArgument)
        self.assertIsInstance(vdf.VDFError("x"), ValueError)
        with self.assertRaises(schema.InvalidArgument):
            vdf.loads_binary(b"\x09nope\x00")

    def test_get_ignores_key_case(self):
        node = vdf.loads_text('"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "x" "1" } } } }')
        self.assertEqual(vdf.get(node, "userlocalconfigstore", "software", "valve", "steam", "X"), "1")
        self.assertEqual(vdf.get(node, "UserLocalConfigStore", "Software"), {"Valve": {"Steam": {"x": "1"}}})
        self.assertIs(vdf.get(node), node)

    def test_get_missing_returns_default(self):
        node = {"a": {"b": "1"}}
        self.assertIsNone(vdf.get(node, "a", "nope"))
        self.assertEqual(vdf.get(node, "nope", default="d"), "d")

    def test_get_through_non_dict_returns_default(self):
        node = {"a": {"b": "1"}}
        self.assertEqual(vdf.get(node, "a", "b", "c", default="d"), "d")
        self.assertEqual(vdf.get("text", "a", default="d"), "d")
        self.assertEqual(vdf.get(None, "a", default="d"), "d")


if __name__ == "__main__":
    unittest.main()
