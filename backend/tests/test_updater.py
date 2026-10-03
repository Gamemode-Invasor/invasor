import asyncio
import hashlib
import io
import json
import tarfile
import unittest
import urllib.error
from unittest import mock

from invasor import updater
from invasor.schema import InvalidArgument, Unavailable

from .helpers import patch, temp_dir

BASE = updater.DOWNLOAD_PREFIX + "v0.2.0/"


def release(tag="v0.2.0", assets=True, **extra):
    version = tag.lstrip("v")
    names = [f"invasor-{version}.tar.gz", f"invasor-{version}.tar.gz.sha256"] if assets else []
    data = {"tag_name": tag, "body": "notes", "assets": [{"name": n, "browser_download_url": updater.DOWNLOAD_PREFIX + f"{tag}/{n}"} for n in names]}
    return {**data, **extra}


def package(version="0.2.0", root=None, extra=(), init=None):
    """A release tarball (bytes) shaped like tools/pack_release.py's."""
    root = root or f"invasor-{version}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {
            "invasor-installation.sh": b"#!/bin/bash\n",
            "backend/invasor/__init__.py": (init or f'__version__ = "{version}"\n').encode(),
        }
        for path, data in files.items():
            info = tarfile.TarInfo(f"{root}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra:
            tar.addfile(info)
    return buf.getvalue()


class Versions(unittest.TestCase):
    def test_plain_versions_compare_numerically(self):
        self.assertEqual(updater.parse_version("v0.10.0"), (0, 10, 0))
        self.assertGreater(updater.parse_version("0.10.0"), updater.parse_version("0.9.0"))

    def test_prereleases_and_junk_are_not_versions(self):
        for bad in ("1.0.0-rc1", "1.0", "latest", "", None, 5):
            with self.subTest(bad=bad):
                self.assertIsNone(updater.parse_version(bad))


class Check(unittest.TestCase):
    def up(self, answer, current="0.1.0"):
        def fetch(url):
            self.assertEqual(url, updater.RELEASE_URL)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return updater.Updater(current, fetch=fetch)

    def test_newer_tag_is_available(self):
        res = self.up(release("v0.2.0")).check()
        self.assertEqual((res["available"], res["latest"], res["checked"], res["notes"]), (True, "0.2.0", True, "notes"))

    def test_same_or_older_is_up_to_date(self):
        for tag in ("v0.1.0", "v0.0.9"):
            with self.subTest(tag=tag):
                res = self.up(release(tag)).check()
                self.assertEqual((res["available"], res["checked"]), (False, True))

    def test_no_release_offline_or_odd_answers_are_just_not_checked(self):
        err = urllib.error.HTTPError(updater.RELEASE_URL, 404, "Not Found", {}, None)
        for answer in (err, OSError("offline"), ValueError("bad json"), [], release("v0.2.0", draft=True),
                       release("v0.2.0", prerelease=True), release("nightly"), release("v0.2.0", assets=False)):
            with self.subTest(answer=repr(answer)[:40]):
                up = self.up(answer)
                res = up.check()
                self.assertEqual((res["checked"], res["available"], res["latest"]), (False, False, None))
                self.assertIs(up.last, res)

    def test_assets_outside_the_projects_releases_are_ignored(self):
        rel = release("v0.2.0")
        for a in rel["assets"]:
            a["browser_download_url"] = "https://evil.example/" + a["name"]
        self.assertFalse(self.up(rel).check()["checked"])


class Apply(unittest.TestCase):
    def setUp(self):
        self.cache = temp_dir(self) / "cache"
        patch(self, "invasor.config.CACHE_DIR", self.cache)
        self.started = []

    def up(self, tar_bytes, sha=None, current="0.1.0", fail=None):
        def download(url, dest, limit):
            if fail:
                raise OSError(fail)
            if url.endswith(".sha256"):
                digest = sha if sha is not None else hashlib.sha256(tar_bytes).hexdigest()
                dest.write_text(f"{digest}  invasor-0.2.0.tar.gz\n")
            else:
                dest.write_bytes(tar_bytes)
        up = updater.Updater(current, fetch=lambda url: release("v0.2.0"), fetch_file=download,
                             start=lambda root, work: self.started.append((root, work)))
        return up

    def leftovers(self):
        return list(self.cache.iterdir()) if self.cache.exists() else []

    def test_good_package_is_unpacked_and_handed_to_the_installer(self):
        res = self.up(package()).apply()
        self.assertEqual(res, {"version": "0.2.0"})
        (root, work), = self.started
        self.assertEqual(root.name, "invasor-0.2.0")
        self.assertTrue((root / "invasor-installation.sh").is_file())
        self.assertEqual(work, root.parent)  # the installer's script removes it afterwards

    def test_nothing_to_install_when_up_to_date(self):
        with self.assertRaises(InvalidArgument):
            self.up(package(), current="0.2.0").apply()
        self.assertEqual(self.started, [])

    def test_wrong_checksum_is_refused_and_cleaned_up(self):
        for sha in ("0" * 64, "nonsense", ""):
            with self.subTest(sha=sha), self.assertRaises(InvalidArgument):
                self.up(package(), sha=sha).apply()
        self.assertEqual(self.started, [])
        self.assertEqual(self.leftovers(), [])

    def test_download_failure_is_unavailable(self):
        with self.assertRaises(Unavailable):
            self.up(package(), fail="offline").apply()
        self.assertEqual(self.leftovers(), [])

    def test_unsafe_packages_are_refused(self):
        link = tarfile.TarInfo("invasor-0.2.0/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        evil = tarfile.TarInfo("invasor-0.2.0/../../escape")
        for name, data in (
            ("symlink", package(extra=[link])),
            ("dotdot", package(extra=[evil])),
            ("other root", package(root="other")),
            ("wrong version inside", package(init='__version__ = "0.3.0"\n')),
        ):
            with self.subTest(name), self.assertRaises(InvalidArgument):
                self.up(data).apply()
        self.assertEqual(self.started, [])
        self.assertEqual(self.leftovers(), [])

    def test_a_failing_start_cleans_up(self):
        up = self.up(package())
        up._start = mock.Mock(side_effect=Unavailable("no systemd"))
        with self.assertRaises(Unavailable):
            up.apply()
        self.assertEqual(self.leftovers(), [])


class Background(unittest.TestCase):
    def run_once(self, up, cfg, steam):
        """One pass of run(): its first sleep is skipped, the second stops it."""
        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) > 1:
                raise asyncio.CancelledError

        async def go():
            with mock.patch("invasor.updater.asyncio.sleep", fake_sleep):
                try:
                    await updater.run(up, cfg, steam)
                except asyncio.CancelledError:
                    pass
        asyncio.run(go())
        return sleeps[1]

    def setUp(self):
        patch(self, "invasor.config.CONFIG_FILE", temp_dir(self) / "config.json")
        self.steam = mock.Mock(notify=mock.AsyncMock())

    def up(self, tag="v0.2.0"):
        return updater.Updater("0.1.0", fetch=lambda url: release(tag))

    def test_new_version_is_announced_once(self):
        cfg = {"update_check": True}
        self.assertEqual(self.run_once(self.up(), cfg, self.steam), updater.CHECK_EVERY)
        self.steam.notify.assert_awaited_once()
        self.assertEqual(cfg["update_last_notified"], "0.2.0")
        self.assertEqual(json.loads((updater.config.CONFIG_FILE).read_text())["update_last_notified"], "0.2.0")
        self.run_once(self.up(), cfg, self.steam)
        self.steam.notify.assert_awaited_once()

    def test_steam_not_ready_retries_soon_and_doesnt_mark_it_announced(self):
        self.steam.notify.side_effect = Unavailable("no steam")
        cfg = {}
        self.assertEqual(self.run_once(self.up(), cfg, self.steam), updater.RETRY_NO_STEAM)
        self.assertNotIn("update_last_notified", cfg)

    def test_switched_off_or_up_to_date_is_silent(self):
        self.assertEqual(self.run_once(self.up(), {"update_check": False}, self.steam), updater.WHEN_OFF)
        self.run_once(self.up("v0.1.0"), {}, self.steam)
        self.steam.notify.assert_not_awaited()

    def test_unexpected_errors_never_end_the_task(self):
        # A task that ends stops the whole service: only the log hears about it.
        for failure in (OSError("disk full"), TimeoutError("steam"), RuntimeError("bug")):
            with self.subTest(failure=repr(failure)):
                self.steam.notify.side_effect = failure
                self.assertEqual(self.run_once(self.up(), {}, self.steam), updater.RETRY_NO_NET)
        self.steam.notify.side_effect = None
        patch(self, "invasor.updater.JsonStore", mock.Mock(side_effect=OSError("read-only")))
        self.assertEqual(self.run_once(self.up(), {}, self.steam), updater.RETRY_NO_NET)

    def test_offline_retries_in_an_hour(self):
        up = updater.Updater("0.1.0", fetch=mock.Mock(side_effect=OSError("offline")))
        self.assertEqual(self.run_once(up, {}, self.steam), updater.RETRY_NO_NET)


if __name__ == "__main__":
    unittest.main()
