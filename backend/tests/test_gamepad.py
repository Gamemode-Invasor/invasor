import unittest

from invasor.gamepad import (
    DECK_BUTTONS,
    STEAM_CONTROLLER_BUTTONS,
    deck_pressed,
    parse_combo,
    steam_controller_pressed,
)


def deck_report(*names):
    """A synthetic 64-byte Steam Deck state report with the given buttons held."""
    data = bytearray(64)
    data[0], data[2] = 0x01, 0x09
    for n in names:
        byte, bit = DECK_BUTTONS[n]
        data[byte] |= 1 << bit
    return bytes(data)


class Combo(unittest.TestCase):
    def test_valid_names_case_insensitive(self):
        self.assertEqual(parse_combo(["l3", "R3"]), {"L3", "R3"})
        self.assertEqual(parse_combo(["QAM", "A"]), {"QAM", "A"})

    def test_bad_config_falls_back_to_l3_r3(self):
        for bad in (["L3", "PADDLE9"], [], None):
            with self.subTest(combo=bad), self.assertLogs("invasor.gamepad", "ERROR"):
                self.assertEqual(parse_combo(bad), {"L3", "R3"})


class DeckReports(unittest.TestCase):
    def test_idle_report_has_no_buttons(self):
        # Real idle report captured from the Legion Go's virtual Deck pad.
        idle = bytes.fromhex("01000940ac05") + bytes(58)
        self.assertEqual(deck_pressed(idle), set())

    def test_each_button_decodes_alone(self):
        for name in DECK_BUTTONS:
            with self.subTest(button=name):
                self.assertEqual(deck_pressed(deck_report(name)), {name})

    def test_combo(self):
        self.assertEqual(deck_pressed(deck_report("L3", "R3")), {"L3", "R3"})

    def test_other_reports_are_ignored(self):
        other = bytearray(deck_report("A"))
        other[2] = 0x04  # not a Deck state report
        self.assertIsNone(deck_pressed(bytes(other)))
        self.assertIsNone(deck_pressed(b"\x01\x00"))


# Real idle state report of a 2026 Steam Controller over Bluetooth (28de:1303);
# byte 4 bit 4 and byte 5 are touch sensors, not buttons.
SC_IDLE = bytes.fromhex(
    "45600000103100000000cc02d9fe7f02db02000000000000000000000000128667335f00fef5303f030003000200"
)


def sc_report(buttons_hex):
    """SC_IDLE with bytes 2-4 replaced, as captured from the real pad."""
    return SC_IDLE[:2] + bytes.fromhex(buttons_hex) + SC_IDLE[5:]


class SteamControllerReports(unittest.TestCase):
    def test_idle_report_has_no_buttons(self):
        self.assertEqual(steam_controller_pressed(SC_IDLE), set())

    def test_real_presses(self):
        cases = {
            "000010": set(),  # touch sensor only
            "008010": {"L3"},
            "200010": {"R3"},
            "208010": {"L3", "R3"},
            "100000": {"QAM"},
            "000011": {"GUIDE"},
            "000200": {"RB"},
            "000008": {"LB"},
        }
        for hexbytes, held in cases.items():
            with self.subTest(report=hexbytes):
                self.assertEqual(steam_controller_pressed(sc_report(hexbytes)), held)

    def test_usb_report(self):
        # Real idle state report over the USB-C cable (28de:1302): id 0x42, 54 bytes.
        usb = bytearray.fromhex(
            "4277000000000000000082005e00a500bf0300000000000000000000000033a3d7005002c7f7fa3e010002000300847f9df718fe17f9"
        )
        self.assertEqual(steam_controller_pressed(bytes(usb)), set())
        usb[2:5] = bytes.fromhex("208000")
        self.assertEqual(steam_controller_pressed(bytes(usb)), {"L3", "R3"})

    def test_each_button_decodes_alone(self):
        for name, (byte, bit) in STEAM_CONTROLLER_BUTTONS.items():
            data = bytearray(SC_IDLE)
            data[2:5] = bytes(3)
            data[byte] |= 1 << bit
            with self.subTest(button=name):
                self.assertEqual(steam_controller_pressed(bytes(data)), {name})

    def test_layouts_dont_cross(self):
        # Each decoder only takes its own reports: a Steam Controller report is
        # never read as a Deck one, and the other way round.
        self.assertIsNone(deck_pressed(sc_report("208010")))
        self.assertIsNone(steam_controller_pressed(deck_report("L3", "R3")))
        self.assertIsNone(steam_controller_pressed(b"\x45\x00"))

    def test_combo_buttons_are_valid_names(self):
        self.assertEqual(parse_combo(["QAM", "R4", "L5"]), {"QAM", "R4", "L5"})


if __name__ == "__main__":
    unittest.main()
