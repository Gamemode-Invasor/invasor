import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from invasor import market

from .helpers import temp_dir

SPEC = importlib.util.spec_from_file_location("gen_market", Path(__file__).resolve().parents[2] / "tools" / "gen_market.py")
gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen)

NOW = datetime(2026, 10, 9, 12, 7, tzinfo=timezone.utc)


def card(mid, version, tag=None):
    return {"repo": f"invasor-{mid}", "id": mid, "version": version, "tag": tag or f"v{version}", "name": mid.title(),
            "description": "d", "author": "A", "min_core": None,
            "zip": "https://github.com/x/y.zip", "sha256": "https://github.com/x/y.zip.sha256"}


def entries(table):
    """entry(repo, beta) answering from {(repo, beta): card or reason}."""
    return lambda repo, beta: table[(repo, beta)]


class Build(unittest.TestCase):
    def test_stable_and_beta_in_the_order_of_repos_conf_without_urls(self):
        table = {("invasor-b", False): card("b", "1.0.0"), ("invasor-b", True): card("b", "1.1.0-rc1"),
                 ("invasor-a", False): card("a", "0.2.0"), ("invasor-a", True): card("a", "0.2.0")}
        catalog, problems = gen.build_catalog(["invasor-b", "invasor-a"], entries(table), NOW)
        self.assertEqual(problems, [])
        self.assertEqual(catalog["generated"], "2026-10-09T12:07:00Z")
        self.assertEqual([m["repo"] for m in catalog["modules"]], ["invasor-b", "invasor-a"])
        self.assertEqual((catalog["modules"][0]["stable"]["version"], catalog["modules"][0]["beta"]["version"]), ("1.0.0", "1.1.0-rc1"))
        self.assertEqual(sorted(catalog["modules"][0]["stable"]), sorted(gen.CARD_KEYS))
        self.assertNotIn("zip", json.dumps(catalog))

    def test_only_pre_releases_means_no_stable_card(self):
        table = {("invasor-a", False): market.NO_RELEASE, ("invasor-a", True): card("a", "0.1.0-rc1")}
        catalog, problems = gen.build_catalog(["invasor-a"], entries(table), NOW)
        self.assertEqual((problems, catalog["modules"][0]["stable"], catalog["modules"][0]["beta"]["version"]), ([], None, "0.1.0-rc1"))

    def test_a_repository_without_a_release_or_with_a_malformed_one_is_skipped(self):
        table = {("invasor-a", False): market.NO_RELEASE, ("invasor-a", True): market.NO_RELEASE,
                 ("invasor-b", False): "its release has no module zip with a .sha256", ("invasor-b", True): "its release has no module zip with a .sha256",
                 ("invasor-c", False): card("c", "1.0.0"), ("invasor-c", True): card("c", "1.0.0")}
        catalog, problems = gen.build_catalog(["invasor-a", "invasor-b", "invasor-c"], entries(table), NOW)
        self.assertEqual(problems, [])
        self.assertEqual([m["repo"] for m in catalog["modules"]], ["invasor-c"])
        self.assertEqual(catalog["skipped"], ["invasor-a: has no release yet", "invasor-b: its release has no module zip with a .sha256"])

    def test_github_failing_is_a_problem_not_a_missing_module(self):
        for reason in ("GitHub answered 502", "error: timed out", "module.json answered 503"):
            with self.subTest(reason=reason):
                table = {("invasor-a", False): reason, ("invasor-a", True): card("a", "1.0.0")}
                _, problems = gen.build_catalog(["invasor-a"], entries(table), NOW)
                self.assertEqual(len(problems), 1)
                self.assertIn("invasor-a", problems[0])

    def test_module_json_not_found_is_the_repositorys_own_problem(self):
        table = {("invasor-a", False): "module.json answered 404", ("invasor-a", True): "module.json answered 404"}
        _, problems = gen.build_catalog(["invasor-a"], entries(table), NOW)
        self.assertEqual(problems, [])


class Main(unittest.TestCase):
    def run_main(self, table, conf="invasor\ninvasor-a\n"):
        d = temp_dir(self)
        (d / "repos.conf").write_text(conf)
        out = d / "out" / "market.json"
        with mock.patch.object(market.Market, "entry", lambda self, repo, beta: table[(repo, beta)]):
            gen.main(["--conf", str(d / "repos.conf"), "--out", str(out)])
        return out

    def test_writes_a_file_the_app_accepts(self):
        out = self.run_main({("invasor-a", False): card("a", "0.2.0"), ("invasor-a", True): card("a", "0.2.0")})
        entries_, notes, generated = market.Market()._from_file(out.read_text(), False)
        self.assertEqual((list(entries_), notes), (["invasor-a"], []))
        self.assertEqual(out.read_text(), json.dumps(json.loads(out.read_text()), indent=2, ensure_ascii=False) + "\n")

    def test_nothing_is_written_with_no_modules_or_a_github_failure(self):
        for table in ({("invasor-a", False): market.NO_RELEASE, ("invasor-a", True): market.NO_RELEASE},
                      {("invasor-a", False): "GitHub answered 500", ("invasor-a", True): card("a", "1.0.0")}):
            with self.subTest(table=str(table)[:40]):
                d = temp_dir(self)
                (d / "repos.conf").write_text("invasor-a\n")
                out = d / "market.json"
                with mock.patch.object(market.Market, "entry", lambda self, repo, beta: table[(repo, beta)]):
                    with self.assertRaises(SystemExit):
                        gen.main(["--conf", str(d / "repos.conf"), "--out", str(out)])
                self.assertFalse(out.exists())

    def test_an_exception_reaching_github_counts_as_a_failure(self):
        d = temp_dir(self)
        (d / "repos.conf").write_text("invasor-a\n")

        def boom(self, repo, beta):
            raise OSError("offline")

        with mock.patch.object(market.Market, "entry", boom):
            with self.assertRaises(SystemExit):
                gen.main(["--conf", str(d / "repos.conf"), "--out", str(d / "market.json")])
        self.assertFalse((d / "market.json").exists())


if __name__ == "__main__":
    unittest.main()
