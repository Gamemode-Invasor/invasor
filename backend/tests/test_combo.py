import unittest

from invasor.combo import decide_combo


def win(role, open=False, focused=False):
    return {"role": role, "open": open, "focused": focused}


class Combo(unittest.TestCase):
    def test_game_in_front_no_steam_ui_does_nothing(self):
        # Reported bug: opening here left an invisible panel that ate Steam/··· presses.
        self.assertEqual(decide_combo([win("main"), win("quickaccess")]), "ignore")
        self.assertEqual(decide_combo([]), "ignore")

    def test_opens_where_the_focus_is(self):
        self.assertEqual(decide_combo([win("main", focused=True), win("quickaccess")]), "open")
        self.assertEqual(decide_combo([win("main"), win("quickaccess", focused=True)]), "open")

    def test_any_open_panel_closes(self):
        self.assertEqual(decide_combo([win("main"), win("quickaccess", open=True)]), "close")
        self.assertEqual(decide_combo([win("main", open=True, focused=True)]), "close")


class NotAvailable(unittest.TestCase):
    def test_quick_access_without_a_game_is_ignored(self):
        self.assertEqual(decide_combo([{"role": "quickaccess", "focused": True, "available": False, "open": False}]), "ignore")
        self.assertEqual(decide_combo([{"role": "quickaccess", "focused": True, "available": True, "open": False}]), "open")
        self.assertEqual(decide_combo([{"role": "main", "focused": True}]), "open")  # older overlays: available


if __name__ == "__main__":
    unittest.main()
