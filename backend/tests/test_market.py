import hashlib
import json
import unittest
import urllib.error

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
