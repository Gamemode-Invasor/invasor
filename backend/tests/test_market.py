import copy
import hashlib
import json
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

from invasor import config, install, market
from invasor.schema import InvalidArgument, Unavailable

from .helpers import patch, temp_dir

CONF = """# comment
invasor
invasor-patito
invasor-pescao   # trailing comment

invasor-patito
../evil
not a name
"""


def release(repo, mid, version, tag=None, prerelease=False, assets=True):
    tag = tag or f"v{version}"
    names = [f"{mid}-{version}.zip", f"{mid}-{version}.zip.sha256"] if assets else []
    base = f"https://github.com/{market.ORG}/{repo}/releases/download/{tag}/"
    return {"tag_name": tag, "prerelease": prerelease, "assets": [{"name": n, "browser_download_url": base + n} for n in names]}


def manifest(name, version, **extra):
    return json.dumps({"api": 1, "name": name, "version": version, "author": "Someone <a@b.c>", "description": f"{name} does things", **extra})


def api(repo):
    return f"https://api.github.com/repos/{market.ORG}/{repo}/releases"


def raw(repo, tag, mid):
    return f"https://raw.githubusercontent.com/{market.ORG}/{repo}/{tag}/{mid}/module.json"


class Net:
    """Answers by URL; an Exception value is raised."""

    def __init__(self, answers):
        self.answers, self.asked = dict(answers), []

    def get(self, url, *_):
        self.asked.append(url)
        res = self.answers[url]
        if isinstance(res, Exception):
            raise res
        return res

    def not_found(self):
        return urllib.error.HTTPError("u", 404, "Not Found", {}, None)


class Parse(unittest.TestCase):
    def test_repos_conf(self):
        self.assertEqual(market.parse_repos_conf(CONF), ["invasor-patito", "invasor-pescao"])

    def test_empty_and_too_many(self):
        self.assertEqual(market.parse_repos_conf(""), [])
        many = "\n".join(f"invasor-m{i}" for i in range(100))
        self.assertEqual(len(market.parse_repos_conf(many)), market.MAX_REPOS)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.net = Net({
            market.CATALOG_URL: CONF,
            api("invasor-patito") + "/latest": release("invasor-patito", "patito", "0.2.1"),
            raw("invasor-patito", "v0.2.1", "patito"): manifest("Patito", "0.2.1", min_core="0.1.3"),
            api("invasor-pescao") + "/latest": release("invasor-pescao", "pescao", "0.2.1"),
            raw("invasor-pescao", "v0.2.1", "pescao"): manifest("Pescao", "0.2.1"),
        })
        self.installed = {}
        self.channel = "stable"
        self.core = "0.1.4"

    def market(self):
        return market.Market(installed=lambda: self.installed, channel=lambda: self.channel, core_version=self.core,
                             fetch=self.net.get, fetch_text=self.net.get, fetch_file=lambda *a: None)

    def test_a_card_per_repository_sorted_by_name(self):
        res = self.market().list()
        self.assertEqual([c["name"] for c in res["modules"]], ["Patito", "Pescao"])
        card = res["modules"][0]
        self.assertEqual((card["repo"], card["id"], card["version"], card["author"], card["min_core"]),
                         ("invasor-patito", "patito", "0.2.1", "Someone", "0.1.3"))
        self.assertEqual((card["installed_version"], card["compatible"], card["problem"]), (None, True, None))
        self.assertNotIn("zip", card)  # URLs never reach the client
        self.assertEqual(res["notes"], [])

    def test_installed_version_and_compatibility(self):
        self.installed = {"patito": "0.2.0"}
        self.core = "0.1.2"
        cards = {c["id"]: c for c in self.market().list()["modules"]}
        self.assertEqual(cards["patito"]["installed_version"], "0.2.0")
        self.assertFalse(cards["patito"]["compatible"])
        self.assertIn("0.1.3", cards["patito"]["problem"])
        self.assertTrue(cards["pescao"]["compatible"])

    def test_what_installing_does(self):
        self.installed = {"patito": "0.2.0", "pescao": "0.2.1"}
        self.assertEqual({c["id"]: c["change"] for c in self.market().list()["modules"]}, {"patito": "update", "pescao": "same"})
        self.installed = {"patito": "0.3.0", "pescao": "0.2.2-rc1"}
        self.assertEqual({c["id"]: c["change"] for c in self.market().list(refresh=True)["modules"]}, {"patito": "downgrade", "pescao": "downgrade"})
        self.installed = {"patito": "weird"}
        self.assertEqual({c["id"]: c["change"] for c in self.market().list(refresh=True)["modules"]}, {"patito": "update", "pescao": None})

    def test_a_broken_repository_is_skipped_with_a_note(self):
        self.net.answers[api("invasor-pescao") + "/latest"] = self.net.not_found()
        res = self.market().list()
        self.assertEqual([c["id"] for c in res["modules"]], ["patito"])
        self.assertEqual(len(res["notes"]), 1)
        self.assertIn("invasor-pescao", res["notes"][0])

    def test_invalid_module_json_or_version_mismatch_is_skipped(self):
        self.net.answers[raw("invasor-patito", "v0.2.1", "patito")] = "not json"
        self.net.answers[raw("invasor-pescao", "v0.2.1", "pescao")] = manifest("Pescao", "9.9.9")
        res = self.market().list()
        self.assertEqual(res["modules"], [])
        self.assertEqual(len(res["notes"]), 2)

    def test_release_without_zip_or_sha_or_outside_the_repo_has_no_card(self):
        self.net.answers[api("invasor-patito") + "/latest"] = release("invasor-patito", "patito", "0.2.1", assets=False)
        rel = release("invasor-pescao", "pescao", "0.2.1")
        for a in rel["assets"]:
            a["browser_download_url"] = "https://evil.example/" + a["name"]
        self.net.answers[api("invasor-pescao") + "/latest"] = rel
        res = self.market().list()
        self.assertEqual(res["modules"], [])
        self.assertEqual(len(res["notes"]), 2)

    def test_another_repositorys_release_assets_are_refused(self):
        rel = release("invasor-patito", "patito", "0.2.1")
        for a in rel["assets"]:
            a["browser_download_url"] = f"https://github.com/{market.ORG}/other/releases/download/v0.2.1/" + a["name"]
        self.net.answers[api("invasor-patito") + "/latest"] = rel
        self.assertEqual([c["id"] for c in self.market().list()["modules"]], ["pescao"])

    def test_the_list_of_repositories_unreachable_is_unavailable(self):
        self.net.answers[market.CATALOG_URL] = OSError("offline")
        with self.assertRaises(Unavailable):
            self.market().list()

    def test_cached_until_refreshed_and_per_channel(self):
        m = self.market()
        m.list()
        n = len(self.net.asked)
        m.list()
        self.assertEqual(len(self.net.asked), n)
        m.list(refresh=True)
        self.assertGreater(len(self.net.asked), n)
        n = len(self.net.asked)
        self.channel = "beta"
        self.net.answers[api("invasor-patito")] = [release("invasor-patito", "patito", "0.3.0-rc1", prerelease=True), release("invasor-patito", "patito", "0.2.1")]
        self.net.answers[api("invasor-pescao")] = [release("invasor-pescao", "pescao", "0.2.1")]
        self.net.answers[api("invasor-patito") + "?per_page=30"] = self.net.answers[api("invasor-patito")]
        self.net.answers[api("invasor-pescao") + "?per_page=30"] = self.net.answers[api("invasor-pescao")]
        self.net.answers[raw("invasor-patito", "v0.3.0-rc1", "patito")] = manifest("Patito", "0.3.0-rc1")
        cards = {c["id"]: c["version"] for c in m.list()["modules"]}
        self.assertEqual(cards, {"patito": "0.3.0-rc1", "pescao": "0.2.1"})
        self.assertGreater(len(self.net.asked), n)

    def test_stable_channel_never_shows_a_pre_release(self):
        self.net.answers[api("invasor-patito") + "/latest"] = release("invasor-patito", "patito", "0.3.0-rc1", prerelease=True)
        self.assertEqual([c["id"] for c in self.market().list()["modules"]], ["pescao"])


def file_card(mid="patito", version="0.2.1", **extra):
    return {"id": mid, "version": version, "tag": f"v{version}", "name": mid.title(), "description": "d",
            "author": "Someone", "min_core": None, **extra}


def market_file(generated=None, **extra):
    generated = generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"schema": 1, "generated": generated, "skipped": [], "modules": [
        {"repo": "invasor-patito", "stable": file_card(), "beta": file_card(version="0.3.0-rc1")},
        {"repo": "invasor-pescao", "stable": file_card("pescao"), "beta": None},
    ], **extra}
    return json.dumps(data)


class FromFile(unittest.TestCase):
    def market(self, answers=None, **kw):
        self.net = Net(answers or {})
        return market.Market(fetch=self.net.get, fetch_text=self.net.get, fetch_file=lambda *a: None, **kw)

    def parse(self, text, beta=False, **kw):
        return self.market()._from_file(text, beta, **kw)

    def test_cards_are_rebuilt_with_urls_inside_the_organisations_releases(self):
        entries, notes, generated = self.parse(market_file())
        card = entries["invasor-patito"]
        self.assertEqual(card["zip"], f"https://github.com/{market.ORG}/invasor-patito/releases/download/v0.2.1/patito-0.2.1.zip")
        self.assertEqual(card["sha256"], card["zip"] + ".sha256")
        self.assertEqual(sorted(entries), ["invasor-patito", "invasor-pescao"])
        self.assertTrue(generated.endswith("Z"))

    def test_urls_in_the_file_are_ignored(self):
        data = json.loads(market_file())
        data["modules"][0]["stable"].update(zip="https://evil.example/x.zip", sha256="https://evil.example/x")
        card = self.parse(json.dumps(data))[0]["invasor-patito"]
        self.assertTrue(card["zip"].startswith(f"https://github.com/{market.ORG}/invasor-patito/releases/download/"))

    def test_stable_and_beta_channels(self):
        stable, notes, _ = self.parse(market_file())
        beta, beta_notes, _ = self.parse(market_file(), beta=True)
        self.assertEqual((stable["invasor-patito"]["version"], beta["invasor-patito"]["version"]), ("0.2.1", "0.3.0-rc1"))
        self.assertNotIn("invasor-pescao", beta)  # null: nothing on that channel
        self.assertEqual(beta_notes, ["invasor-pescao: has no release yet"])

    def test_a_file_that_cannot_be_used_raises(self):
        old = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for text in ("not json", "[]", market_file(generated=old), market_file(generated="yesterday"),
                     json.dumps({"schema": 2, "generated": "2026-10-09T00:00:00Z", "modules": []}),
                     json.dumps({"schema": 1, "generated": "2026-10-09T00:00:00Z"}),
                     "x" * (market.MAX_FILE + 1)):
            with self.subTest(text=text[:40]):
                with self.assertRaises(ValueError):
                    self.parse(text)

    def test_an_invalid_card_is_skipped_with_a_note_and_the_rest_stays(self):
        for bad in ({"id": "Bad Id"}, {"tag": "latest"}, {"version": "9.9.9"}, {"name": ""}, {"min_core": "v1.0.0"}, {"description": 5}):
            with self.subTest(bad=bad):
                data = json.loads(market_file())
                data["modules"][0]["stable"].update(bad)
                entries, notes, _ = self.parse(json.dumps(data))
                self.assertEqual(sorted(entries), ["invasor-pescao"])
                self.assertEqual(len(notes), 1)
                self.assertIn("invasor-patito", notes[0])

    def test_odd_repositories_are_ignored(self):
        data = json.loads(market_file())
        data["modules"] += [{"repo": "../x", "stable": file_card()}, {"repo": "invasor", "stable": file_card()}, "x", {"repo": 5}]
        self.assertEqual(sorted(self.parse(json.dumps(data))[0]), ["invasor-patito", "invasor-pescao"])

    def test_long_texts_are_clipped_and_one_line(self):
        data = json.loads(market_file())
        data["modules"][0]["stable"].update(name="A\nB" + "x" * 500, description="d" * 5000, author="a\nb")
        card = self.parse(json.dumps(data))[0]["invasor-patito"]
        self.assertEqual((len(card["name"]) <= market.NAME_MAX, len(card["description"]), card["author"]),
                         (True, market.DESCRIPTION_MAX, "a b"))
        self.assertNotIn("\n", card["name"])

    def test_skipped_notes_of_the_file_are_kept_without_repeating_missing_releases(self):
        text = market_file(skipped=["invasor-x: its release has no module zip with a .sha256", "invasor-y: has no release yet", 5])
        notes = self.parse(text)[1]
        self.assertEqual(notes, ["invasor-x: its release has no module zip with a .sha256"])


class FileOrLive(unittest.TestCase):
    def setUp(self):
        self.net = Net({
            market.CATALOG_URL: "invasor-patito\n",
            api("invasor-patito") + "/latest": release("invasor-patito", "patito", "0.2.1"),
            raw("invasor-patito", "v0.2.1", "patito"): manifest("Patito", "0.2.1"),
        })

    def market(self):
        return market.Market(fetch=self.net.get, fetch_text=self.net.get, fetch_file=lambda *a: None)

    def test_the_file_is_enough_and_nothing_else_is_asked(self):
        self.net.answers[market.FILE_URL] = market_file()
        res = self.market().list()
        self.assertEqual((res["source"], [c["id"] for c in res["modules"]]), ("file", ["patito", "pescao"]))
        self.assertTrue(res["generated"])
        self.assertEqual(self.net.asked, [market.FILE_URL])

    def test_a_missing_stale_or_broken_file_falls_back_to_github(self):
        old = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for answer in (urllib.error.HTTPError("u", 404, "Not Found", {}, None), OSError("offline"), "nope", market_file(generated=old)):
            with self.subTest(answer=str(answer)[:30]):
                self.net.answers[market.FILE_URL] = answer
                res = self.market().list()
                self.assertEqual((res["source"], res["generated"], [c["id"] for c in res["modules"]]), ("live", None, ["patito"]))

    def test_a_card_from_the_file_downloads_like_any_other(self):
        home = temp_dir(self)
        patch(self, "invasor.install.HOME", home)
        patch(self, "invasor.config.CACHE_DIR", home / ".cache" / "invasor")
        self.net.answers[market.FILE_URL] = market_file()
        data = b"PK"
        sha = hashlib.sha256(data).hexdigest()
        asked = []

        def fetch_file(url, dest, limit):
            asked.append(url)
            dest.write_bytes(data if url.endswith(".zip") else sha.encode())

        m = market.Market(fetch=self.net.get, fetch_text=self.net.get, fetch_file=fetch_file)
        path, work, mid = m.download("invasor-patito")
        self.assertEqual(mid, "patito")
        self.assertEqual(asked, [f"https://github.com/{market.ORG}/invasor-patito/releases/download/v0.2.1/patito-0.2.1.zip",
                                 f"https://github.com/{market.ORG}/invasor-patito/releases/download/v0.2.1/patito-0.2.1.zip.sha256"])


class Download(unittest.TestCase):
    def setUp(self):
        home = temp_dir(self)
        patch(self, "invasor.install.HOME", home)
        patch(self, "invasor.config.CACHE_DIR", home / ".cache" / "invasor")
        self.data = b"PK zip bytes"
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.files = {}
        self.net = Net({
            market.CATALOG_URL: "invasor-patito\n",
            api("invasor-patito") + "/latest": release("invasor-patito", "patito", "0.2.1"),
            raw("invasor-patito", "v0.2.1", "patito"): manifest("Patito", "0.2.1", min_core="0.1.3"),
        })
        self.core = "0.1.4"
        self.home = home

    def fetch_file(self, url, dest, limit):
        self.files[url] = limit
        dest.write_bytes(self.data if url.endswith(".zip") else (self.sha + "  patito-0.2.1.zip\n").encode())

    def market(self):
        return market.Market(core_version=self.core, fetch=self.net.get, fetch_text=self.net.get, fetch_file=self.fetch_file)

    def test_downloads_verifies_and_returns_a_path_inside_home(self):
        path, work, mid = self.market().download("invasor-patito")
        self.assertEqual(mid, "patito")
        self.assertTrue((self.home / path).is_file())
        self.assertEqual((self.home / path).read_bytes(), self.data)
        self.assertEqual(self.files[f"https://github.com/{market.ORG}/invasor-patito/releases/download/v0.2.1/patito-0.2.1.zip"], install.MAX_ZIP_BYTES)

    def test_a_wrong_checksum_deletes_everything(self):
        self.sha = "0" * 64
        with self.assertRaises(InvalidArgument):
            self.market().download("invasor-patito")
        self.assertEqual(list((self.home / ".cache" / "invasor").iterdir()), [])

    def test_an_unreadable_checksum_is_refused(self):
        self.sha = "nope"
        with self.assertRaises(InvalidArgument):
            self.market().download("invasor-patito")

    def test_only_repositories_of_the_catalog(self):
        m = self.market()
        for repo in ("invasor-other", "../x", "", None, 5):
            with self.assertRaises(InvalidArgument, msg=repr(repo)):
                m.download(repo)

    def test_a_module_that_needs_a_newer_core_is_refused(self):
        self.core = "0.1.2"
        with self.assertRaises(InvalidArgument):
            self.market().download("invasor-patito")

    def test_a_failed_download_is_unavailable_and_leaves_no_files(self):
        def boom(*_):
            raise OSError("down")
        m = self.market()
        m._download = boom
        with self.assertRaises(Unavailable):
            m.download("invasor-patito")
        self.assertEqual(list((self.home / ".cache" / "invasor").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
