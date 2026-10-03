import unittest

from invasor.gamepad import DECK_BUTTONS, deck_pressed, parse_combo


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


if __name__ == "__main__":
    unittest.main()
