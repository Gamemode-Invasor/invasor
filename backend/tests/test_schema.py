import unittest

from invasor import schema


def field(**kw):
    return {"key": "k", "label": "K", **kw}


def parse(settings):
    return schema.parse_manifest({"api": 1, "name": "M", "version": "1", "settings": settings}, "m")


class Snap(unittest.TestCase):
    def test_float_steps_have_no_noise(self):
        self.assertEqual(schema.snap(0.1 + 0.2, 0, 1, 0.1), 0.3)
        self.assertEqual(schema.snap(0.75, 0, 1, 0.25), 0.75)
        self.assertEqual(schema.snap(1.26, 1, 2, 0.05), 1.25)

    def test_clamps_and_counts_from_min(self):
        self.assertEqual(schema.snap(999, 15, 144, 5), 140)  # 144 isn't on a step from 15
        self.assertEqual(schema.snap(-3, 15, 144, 5), 15)
        self.assertEqual(schema.snap(17, 15, 144, 5), 15)
        self.assertEqual(schema.snap(18, 15, 144, 5), 20)

    def test_ints_stay_ints(self):
        self.assertIsInstance(schema.snap(50.0, 0, 100, 5), int)
        self.assertIsInstance(schema.snap(0.5, 0, 1, 0.5), float)


class Manifest(unittest.TestCase):
    def test_minimal(self):
        m = schema.parse_manifest({"api": 1, "name": "Mod", "version": "0.1"}, "mod")
        self.assertEqual((m["tab"], m["order"], m["settings"], m["fields"]), ("Mod", 100, [], {}))

    def test_errors_have_paths(self):
        cases = [
            ({"name": "M", "version": "1"}, "api"),
            ({"api": 2, "name": "M", "version": "1"}, "api"),
            ({"api": 1, "version": "1"}, "name"),
            ({"api": 1, "name": "M"}, "version"),
            ({"api": 1, "name": "M", "version": "1", "order": "1"}, "order"),
            ({"api": 1, "name": "M", "version": "1", "extra": 1}, r"unknown key\(s\): extra"),
            ({"api": True, "name": "M", "version": "1"}, "api"),
        ]
        for data, msg in cases:
            with self.subTest(data=data), self.assertRaisesRegex(schema.SchemaError, msg):
                schema.parse_manifest(data, "m")

    def test_ids(self):
        for bad in ("Demo", "a b", "-x", ""):
            with self.subTest(id=bad), self.assertRaises(schema.SchemaError):
                schema.parse_manifest({"api": 1, "name": "M", "version": "1"}, bad)


class Settings(unittest.TestCase):
    def test_every_type_and_sections(self):
        m = parse([
            field(key="a", type="toggle", default=True),
            field(key="b", type="checkbox", default=False),
            field(key="c", type="slider", min=0, max=1, step=0.1, default=0.3, unit="x"),
            {"section": "S", "open": False, "items": [
                field(key="d", type="number", min=15, max=144, step=5, default=60),
                field(key="e", type="radio", options=[{"value": "x", "label": "X"}, {"value": 2, "label": "2"}], default=2),
                field(key="f", type="select", options=[{"value": True, "label": "Yes"}], default=True),
                field(key="g", type="text", default="", max_length=4, placeholder="…"),
                field(key="h", type="password", default="", max_length=8),
            ]},
        ])
        self.assertEqual(list(m["fields"]), list("abcdefgh"))
        self.assertEqual(m["settings"][3]["section"], "S")
        self.assertFalse(m["settings"][3]["open"])
        self.assertEqual(m["fields"]["g"]["max_length"], 4)
        self.assertEqual(m["fields"]["a"].get("hint"), None)

    def test_min_core(self):
        base = {"api": 1, "name": "M", "version": "1"}
        self.assertIsNone(schema.parse_manifest(base, "m")["min_core"])
        m = schema.parse_manifest({**base, "min_core": "0.1.3"}, "m")
        self.assertEqual(m["min_core"], "0.1.3")
        for bad in ("v0.1.3", "1.2", "x", "", 1, ["0.1.3"], "0.1.3-beta"):
            with self.subTest(bad=bad), self.assertRaisesRegex(schema.SchemaError, "min_core"):
                schema.parse_manifest({**base, "min_core": bad}, "m")

    def test_core_problem(self):
        need = lambda v: {"min_core": v}  # noqa: E731
        self.assertIsNone(schema.core_problem({"min_core": None}, "0.1.0"))
        self.assertIsNone(schema.core_problem({}, "0.1.0"))
        self.assertIsNone(schema.core_problem(need("0.1.3"), "0.1.3"))
        self.assertIsNone(schema.core_problem(need("0.1.3"), "0.2.0"))
        self.assertIsNone(schema.core_problem(need("0.1.3-rc1"), "0.1.3-rc2"))
        self.assertIsNone(schema.core_problem(need("0.1.3"), "dev"))
        self.assertEqual(schema.core_problem(need("0.1.3"), "0.1.2"), "needs Invasor 0.1.3 or newer (this is 0.1.2)")
        self.assertIn("0.1.3-rc2", schema.core_problem(need("0.1.3"), "0.1.3-rc2"))  # an rc is older than its release
        self.assertIn("needs", schema.core_problem(need("0.10.0"), "0.9.9"))

    def test_password_errors_never_carry_the_value(self):
        f = parse([field(key="p", type="password", default="", max_length=4)])["fields"]["p"]
        for bad in (12345, ["hunter2"], None):
            with self.assertRaises(schema.InvalidArgument) as cm:
                schema.coerce(f, bad)
            self.assertNotIn(str(bad), str(cm.exception))
        self.assertEqual(schema.coerce(f, "abcd"), "abcd")

    def test_field_errors(self):
        cases = [
            ([field(type="nope", default=1)], r"settings\[0\]\.type"),
            ([field(type="toggle")], r"settings\[0\]\.default: is required"),
            ([field(type="toggle", default=1)], r"settings\[0\]\.default"),
            ([field(type="slider", min=0, max=10, default=11)], r"settings\[0\]\.default"),
            ([field(type="slider", min=0, max=10, step=3, default=4)], "on a step"),
            ([field(type="slider", min=5, max=5, default=5)], r"\.max"),
            ([field(type="slider", min=0, max="9", default=5)], r"\.max: must be a number"),
            ([field(type="slider", min=0, max=9, step=0, default=5)], r"\.step"),
            ([field(type="select", options=[], default=1)], r"\.options"),
            ([field(type="select", options=[{"value": 1, "label": "a"}, {"value": 1, "label": "b"}], default=1)], "duplicate value"),
            ([field(type="select", options=[{"value": 1, "label": "a"}], default=True)], "not one of the options"),
            ([field(type="text", default="toolong", max_length=3)], "longer than"),
            ([field(type="password", default="hunter2")], "must be empty"),
            ([field(type="password", default="", max_length=0)], r"\.max_length"),
            ([field(key="p", type="password", default=""), field(key="t", type="toggle", default=True, when={"p": "x"})], "can't be a condition"),
            ([field(type="toggle", default=True, mni=1)], "unknown key"),
            ([field(type="toggle", default=True), field(type="toggle", default=True)], "duplicate key"),
            ([field(key="1x", type="toggle", default=True)], r"\.key"),
            ([field(type="toggle", default=True, label="")], r"\.label"),
            ([{"section": "S", "items": []}], r"settings\[0\]\.items"),
            ([{"section": "S", "items": [{"section": "T", "items": []}]}], "nested"),
            ({"k": 1}, "settings: must be a list"),
        ]
        for settings, msg in cases:
            with self.subTest(msg=msg), self.assertRaisesRegex(schema.SchemaError, msg):
                parse(settings)

    def test_coerce(self):
        f = parse([
            field(key="n", type="slider", min=0, max=100, step=5, default=50),
            field(key="o", type="radio", options=[{"value": 1, "label": "1"}, {"value": "1", "label": "one"}], default=1),
            field(key="t", type="toggle", default=True),
        ])["fields"]
        self.assertEqual(schema.coerce(f["n"], 52), 50)
        self.assertEqual(schema.coerce(f["o"], "1"), "1")
        for key, bad in (("n", "5"), ("n", True), ("n", float("nan")), ("n", 10**400), ("o", True), ("o", 2), ("t", 1), ("t", None)):
            with self.subTest(key=key, value=bad), self.assertRaises(ValueError):
                schema.coerce(f[key], bad)

    def test_clean(self):
        fields = parse([
            field(key="a", type="toggle", default=True),
            field(key="b", type="number", min=0, max=10, default=1),
        ])["fields"]
        self.assertEqual(schema.clean(fields, {"a": False, "b": 30, "gone": 1}), {"a": False, "b": 10})
        self.assertEqual(schema.clean(fields, {"a": "x"}), {"a": True, "b": 1})
        self.assertEqual(schema.clean(fields, None), {"a": True, "b": 1})



class Forms(unittest.TestCase):
    def test_named_forms_parse_like_settings(self):
        m = schema.parse_manifest({"api": 1, "name": "M", "version": "1", "forms": {
            "profile": [{"key": "multiplier", "type": "number", "label": "Multiplier", "min": 2, "max": 20, "default": 2}],
            "global": [{"section": "Advanced", "items": [{"key": "dll", "type": "text", "label": "DLL", "default": ""}]}],
        }}, "m")
        self.assertEqual(list(m["form_fields"]["profile"]), ["multiplier"])
        self.assertEqual(m["forms"]["global"][0]["section"], "Advanced")
        self.assertEqual(m["fields"], {})  # forms aren't settings

    def test_form_errors_have_paths(self):
        cases = [
            ([1], "forms: must be an object"),
            ({"1bad": [{"key": "a", "type": "toggle", "label": "A", "default": True}]}, r"forms\.1bad"),
            ({"p": []}, r"forms\.p: a form needs"),
            ({"p": [{"key": "a", "type": "toggle", "label": "A"}]}, r"forms\.p\[0\]\.default"),
        ]
        for forms, msg in cases:
            with self.subTest(msg=msg), self.assertRaisesRegex(schema.SchemaError, msg):
                schema.parse_manifest({"api": 1, "name": "M", "version": "1", "forms": forms}, "m")


class NoQam(unittest.TestCase):
    def test_no_qam(self):
        base = {"api": 1, "name": "M", "version": "1"}
        self.assertFalse(schema.parse_manifest(base, "m")["no_qam"])
        self.assertTrue(schema.parse_manifest({**base, "no_qam": True}, "m")["no_qam"])
        with self.assertRaisesRegex(schema.SchemaError, "no_qam"):
            schema.parse_manifest({**base, "no_qam": "yes"}, "m")


class Author(unittest.TestCase):
    BASE = {"api": 1, "name": "M", "version": "1"}

    def test_optional_free_text(self):
        m = schema.parse_manifest(self.BASE, "m")
        self.assertEqual((m["author"], m["author_name"]), ("", ""))
        m = schema.parse_manifest({**self.BASE, "author": "FranjeGueje"}, "m")
        self.assertEqual((m["author"], m["author_name"]), ("FranjeGueje", "FranjeGueje"))

    def test_the_ui_name_drops_the_email(self):
        m = schema.parse_manifest({**self.BASE, "author": "Jane Doe <jane@example.com>"}, "m")
        self.assertEqual(m["author"], "Jane Doe <jane@example.com>")
        self.assertEqual(m["author_name"], "Jane Doe")
        self.assertEqual(schema.author_name("  Two Words  <a@b.c> "), "Two Words")
        self.assertEqual(schema.author_name("<a@b.c>"), "<a@b.c>")  # only an email: shown as is
        self.assertEqual(schema.author_name("A <b> c"), "A <b> c")

    def test_invalid(self):
        for bad in (3, None, ["x"], "a\nb", "x" * 129):
            with self.subTest(author=bad), self.assertRaisesRegex(schema.SchemaError, "author"):
                schema.parse_manifest({**self.BASE, "author": bad}, "m")


class When(unittest.TestCase):
    FIELDS = [
        {"key": "adaptive", "type": "toggle", "label": "A", "default": False},
        {"key": "mode", "type": "select", "label": "M", "default": "a", "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
        {"key": "n", "type": "number", "label": "N", "min": 2, "max": 5, "default": 2},
    ]

    def parse(self, *extra):
        return schema.parse_settings(self.FIELDS + list(extra))

    def test_fields_and_sections(self):
        tree, _ = self.parse(
            {"key": "target", "type": "number", "label": "T", "min": 30, "max": 240, "default": 60, "when": {"adaptive": True}},
            {"section": "S", "when": {"mode": "b", "n": 3}, "items": [
                {"key": "x", "type": "toggle", "label": "X", "default": False, "when": {"adaptive": False}}]},
        )
        self.assertEqual(tree[3]["when"], {"adaptive": True})
        self.assertEqual(tree[4]["when"], {"mode": "b", "n": 3})
        self.assertEqual(tree[4]["items"][0]["when"], {"adaptive": False})
        self.assertNotIn("when", tree[0])
        # a field may depend on one declared after it
        schema.parse_settings([{"key": "y", "type": "toggle", "label": "Y", "default": False, "when": {"z": True}},
                               {"key": "z", "type": "toggle", "label": "Z", "default": False}])

    def test_lists_and_disabled_when(self):
        tree, _ = self.parse(
            {"key": "t", "type": "toggle", "label": "T", "default": False,
             "when": {"mode": ["a", "b"]}, "disabled_when": {"adaptive": True, "n": [2, 3]}},
        )
        self.assertEqual((tree[3]["when"], tree[3]["disabled_when"]), ({"mode": ["a", "b"]}, {"adaptive": True, "n": [2, 3]}))
        for bad, msg in (({"mode": []}, "empty list"), ({"mode": ["a", "z"]}, "not one of the options"),
                         ({"t": True}, "itself"), ({"nope": 1}, "isn't a field")):
            with self.subTest(bad=bad), self.assertRaisesRegex(schema.SchemaError, rf"settings\[3\]\.disabled_when\..*{msg}"):
                self.parse({"key": "t", "type": "toggle", "label": "T", "default": False, "disabled_when": bad})
        with self.assertRaisesRegex(schema.SchemaError, "unknown key"):  # sections can't be disabled
            self.parse({"section": "S", "disabled_when": {"adaptive": True}, "items": [{"key": "t", "type": "toggle", "label": "T", "default": False}]})

    def test_errors(self):
        cases = {
            "isn't a field": {"nope": True},
            "itself": {"t": True},
            "true/false": {"adaptive": 1},
            "not one of the options": {"mode": "c"},
            "can take": {"n": 9},
            "non-empty object": {},
        }
        for msg, when in cases.items():
            with self.subTest(when=when), self.assertRaisesRegex(schema.SchemaError, msg):
                self.parse({"key": "t", "type": "toggle", "label": "T", "default": False, "when": when})
        with self.assertRaisesRegex(schema.SchemaError, r"settings\[3\]\.when"):
            self.parse({"section": "S", "when": [], "items": [{"key": "t", "type": "toggle", "label": "T", "default": False}]})


if __name__ == "__main__":
    unittest.main()
