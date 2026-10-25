"""Core self-update: look for a newer release on GitHub and install it.

check() asks the GitHub API for the latest release of the project; if its tag is a
higher version than the installed one it reports it as available. apply() downloads that
release's package (the tarball tools/pack_release.py builds), verifies its sha256, unpacks
it and runs its invasor-installation.sh --install. The installer restarts this very service,
which re-injects the new overlay into Steam (no Steam restart), so it runs in a transient
systemd unit that outlives the service.

The sha256 is published next to the package in the same release: it catches a corrupt or
truncated download, not a compromised GitHub account.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, config
from . import archive as archives
from .version import parse_version
from .schema import InvalidArgument, Unavailable

log = logging.getLogger("invasor.updater")

REPO = "Gamemode-Invasor/invasor"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"  # stable channel: never a pre-release
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=30"  # beta channel: pre-releases too
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
USER_AGENT = f"invasor/{__version__}"  # GitHub's API refuses requests without one
TIMEOUT = 20
MAX_JSON = 1 << 20
MAX_PACKAGE = 64 << 20
MAX_UNPACKED = 256 << 20

STARTUP_DELAY = 60  # when nothing says Steam started: let it come up, the notification needs it
SETTLE = 10  # after steam_start: let its UI come up
CHECK_EVERY = 24 * 3600
RETRY_NO_NET = 3600
RETRY_NO_STEAM = 900
WHEN_OFF = 3600  # how often to see whether the user switched checking back on

SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
INIT_VERSION_RE = re.compile(r'^__version__ = "(.*)"$', re.M)

# Runs the installer from the unpacked release, then removes the work folder. $1: the
# unpacked release, $2: the work folder. The installer restarts the service, which
# re-injects the new overlay into Steam's windows: Steam itself is never restarted.
UPDATE_SCRIPT = r"""
bash "$1/invasor-installation.sh" --install || echo "invasor update: the installer failed" >&2
rm -rf "$2"
"""


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def _request(url):
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})


def fetch_json(url):
    with _opener().open(_request(url), timeout=TIMEOUT) as r:
        data = r.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError("answer too large")
    return json.loads(data)


def download(url, dest, limit, progress=None):
    """Save url (https only) at dest, giving up past `limit` bytes. `progress(done, total)`,
    if given, hears how many bytes are in so far (total is None when the server doesn't say)."""
    if not url.startswith("https://"):
        raise ValueError(f"not an https URL: {url}")
    size = 0
    with _opener().open(_request(url), timeout=TIMEOUT) as r:
        if not r.geturl().startswith("https://"):  # a redirect may leave https
            raise ValueError(f"redirected to a non-https URL: {r.geturl()}")
        total = None
        if progress:
            try:
                total = int(r.headers.get("Content-Length") or 0) or None
            except ValueError:
                pass
        with open(dest, "wb") as f:
            while chunk := r.read(1 << 16):
                size += len(chunk)
                if size > limit:
                    raise ValueError("download too large")
                f.write(chunk)
                if progress:
                    progress(size, total)


def verify_sha256(path, sha_text, what):
    """InvalidArgument unless `path` matches the first word of a .sha256 file (`what`: "update", "module"…)."""
    words = sha_text.split()
    if not words or not SHA_RE.match(words[0]):
        raise InvalidArgument(f"the {what}'s checksum file is unreadable")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if not hmac.compare_digest(h.hexdigest(), words[0].lower()):
        raise InvalidArgument(f"the {what}'s checksum doesn't match: not installing it")


def run_detached(unit, argv, what="update"):
    """Run argv in a transient systemd user unit, detached from this service: it outlives
    a restart or an uninstall of the very service that asks for it."""
    cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", *argv]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise Unavailable(f"systemd-run not found: can't {what} from here") from None
    except subprocess.CalledProcessError as e:
        raise Unavailable(f"couldn't start the {what} ({(e.stderr or '').strip() or e.returncode})") from None


def launch(root, workdir):
    """Run the installer from the unpacked release, detached from this service."""
    run_detached("invasor-update", ["bash", "-c", UPDATE_SCRIPT, "invasor-update", str(root), str(workdir)])


def safe_extract(archive, dest, root_name):
    """Unpack the package under dest/<root_name>: only plain files and folders in that single
    folder (no absolute paths, .., links or devices), bounded in size (see archive.py)."""
    unpacked = Path(dest) / ".unpacked"
    top = archives.extract(archive, unpacked, single_root=True, max_bytes=MAX_UNPACKED)
    if top.name != root_name:
        raise InvalidArgument(f"the package holds {top.name!r}, not {root_name!r}")
    root = top.rename(Path(dest) / root_name)
    unpacked.rmdir()
    return root


class Updater:
    def __init__(self, current=__version__, fetch=fetch_json, fetch_file=download, start=launch, channel=lambda: "stable"):
        self.current = current
        self._channel = channel  # "stable" or "beta", asked at each check: it can change while running
        self._fetch, self._download, self._start = fetch, fetch_file, start
        self.last = None  # the latest check()'s answer, for the panel to show on opening

    def _unknown(self, why):
        log.info("couldn't check for updates: %s", why)
        self.last = {"current": self.current, "latest": None, "available": False, "downgrade": False, "checked": False, "notes": ""}
        return self.last

    def check(self):
        """{current, latest, available, downgrade, checked, notes}. Offline, no release yet, a release
        without a package…: checked is False (nothing to report, nothing wrong)."""
        return self._check()[0]

    def _check(self):
        """check()'s answer and the package it found: (package URL, sha256 URL) or None. Handed
        back, not kept in self: the panel's and the probe's checks may run at the same time."""
        beta = self._channel() == "beta"
        try:
            release = self._fetch(RELEASES_URL if beta else RELEASE_URL)
        except urllib.error.HTTPError as e:
            return self._unknown(f"GitHub answered {e.code}"), None
        except Exception as e:
            return self._unknown(e), None
        if beta:
            release = self._newest(release)
        if not isinstance(release, dict) or release.get("draft") or (release.get("prerelease") and not beta):
            return self._unknown("no usable release"), None
        tag = release.get("tag_name")
        latest = parse_version(tag)
        mine = parse_version(self.current)
        if latest is None or mine is None:
            return self._unknown(f"unreadable version ({tag!r} / {self.current!r})"), None
        if latest[3] == 0 and not beta:  # a release candidate nobody marked as a pre-release
            return self._unknown(f"{tag} is a pre-release"), None
        version = tag[1:] if tag.startswith("v") else tag
        package = self._find_package(release, version)
        # A machine on a release candidate that follows the stable channel is offered the stable
        # version even if it is older: the channel says what to follow. Never a downgrade otherwise.
        down = not beta and mine[3] == 0 and latest < mine
        available = latest > mine or down
        if available and package is None:
            return self._unknown(f"release {version} has no package"), None
        notes = release.get("body")
        self.last = {
            "current": self.current,
            "latest": version,
            "available": available,
            "downgrade": down,
            "checked": True,
            "notes": self._summary(notes),
        }
        return self.last, package

    @staticmethod
    def _newest(releases):
        """The highest version among GitHub's list of releases (drafts and odd tags ignored), or None."""
        found = [r for r in releases if isinstance(r, dict) and not r.get("draft") and parse_version(r.get("tag_name"))] \
            if isinstance(releases, list) else []
        return max(found, key=lambda r: parse_version(r["tag_name"]), default=None)

    @staticmethod
    def _summary(body):
        """The short text of a release: what comes before the first line that is only ---
        (RELEASE_NOTES.md; GitHub's own notes follow it). No such line: all of it."""
        if not isinstance(body, str):
            return ""
        head = re.split(r"^---[ \t]*$", body.replace("\r\n", "\n"), maxsplit=1, flags=re.M)[0]
        return head.strip()[:2000]

    @staticmethod
    def _find_package(release, version):
        """(package URL, sha256 URL) of the release's assets, if both are there."""
        urls = {}
        for a in release.get("assets") or []:
            if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str):
                if a["browser_download_url"].startswith(DOWNLOAD_PREFIX):
                    urls[a["name"]] = a["browser_download_url"]
        name = f"invasor-{version}.tar.gz"
        if name in urls and name + ".sha256" in urls:
            return urls[name], urls[name + ".sha256"]
        return None

    def apply(self, task=None):
        """Download, verify and unpack the new version, and hand over to the installer.
        Returns once it's started: the service is restarted by it a moment later.
        `task` (tasks.Task), if given, shows how far it got."""
        res, package = self._check()
        if not res["checked"] or not res["available"]:
            raise InvalidArgument("there's no update to install")
        version, (pkg_url, sha_url) = res["latest"], package
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="update-", dir=config.CACHE_DIR))
        try:
            tarball = work / f"invasor-{version}.tar.gz"
            try:
                # The progress goes only where there is a task to show it (and fakes in tests don't take it).
                self._download(pkg_url, tarball, MAX_PACKAGE, **({"progress": task.progress(0, 0.8, "Downloading")} if task else {}))
                self._download(sha_url, work / "sha256", 1024)
            except Exception as e:
                raise Unavailable(f"couldn't download the update: {e}") from None
            if task:
                task.update(0.8, "Verifying")
            verify_sha256(tarball, (work / "sha256").read_text(errors="replace"), "update")
            if task:
                task.update(0.9, "Unpacking")
            try:
                root = safe_extract(tarball, work, f"invasor-{version}")
            except (ValueError, OSError) as e:
                raise InvalidArgument(f"the update package is invalid: {e}") from None
            self._check_contents(root, version)
            log.info("updating %s -> %s", self.current, version)
            if task:
                task.update(0.95, "Starting the installer")
            self._start(root, work)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return {"version": version}

    @staticmethod
    def _check_contents(root, version):
        script = root / "invasor-installation.sh"
        init = root / "backend" / "invasor" / "__init__.py"
        if not script.is_file() or not init.is_file():
            raise InvalidArgument("the update package is incomplete")
        m = INIT_VERSION_RE.search(init.read_text(errors="replace"))
        if not m or m.group(1) != version:
            raise InvalidArgument(f"the package holds version {m.group(1) if m else '?'}, not {version}")


async def run(updater, cfg, steam, market=None, steam_up=None):
    """Background: look for a new Invasor release and for newer versions of the installed modules
    in the Market when Steam starts (steam_up is set) and then daily, and tell Steam about each
    once. Without steam_up it waits STARTUP_DELAY instead. Switched off by the update_check preference."""
    await _pause(STARTUP_DELAY, steam_up, first=True)
    while True:
        delay = CHECK_EVERY
        try:
            delay = await _round(updater, cfg, steam)
            if market is not None:
                delay = min(delay, await _market_round(market, cfg, steam))
        except Exception:
            # Never let this background task end: a service task that stops stops Invasor.
            log.exception("update check failed")
            delay = RETRY_NO_NET
        await _pause(delay, steam_up)


async def _pause(delay, steam_up, first=False):
    """Sleep `delay` seconds; a (new) Steam start cuts it short, after SETTLE for its UI to come up."""
    if steam_up is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(steam_up.wait(), None if first else delay)
    except asyncio.TimeoutError:
        return
    steam_up.clear()
    await asyncio.sleep(SETTLE)


async def _round(updater, cfg, steam):
    """One pass of run(): seconds until the next one."""
    if not cfg.get("update_check", True):
        return WHEN_OFF
    res = await asyncio.to_thread(updater.check)
    if not res["checked"]:
        return RETRY_NO_NET
    # A way back to stable is for the user to ask for in the panel, not something to announce.
    if res["available"] and not res["downgrade"] and cfg.get("update_last_notified") != res["latest"]:
        try:
            await steam.notify(
                "Invasor update available",
                f"Version {res['latest']} is out (you have {res['current']}). Open Invasor > Settings > Updates.",
            )
        except (Unavailable, InvalidArgument) as e:
            log.info("update notification not shown yet: %s", e)
            return RETRY_NO_STEAM
        config.save(cfg, update_last_notified=res["latest"])
    return CHECK_EVERY


async def _market_round(market, cfg, steam):
    """Tell Steam, once per set of updates, that the Market holds newer versions of installed modules.
    A downgrade (the Market has an older version) or a module this Invasor can't run is not news."""
    if not cfg.get("update_check", True):
        return WHEN_OFF
    try:
        cards = (await asyncio.to_thread(market.list))["modules"]
    except Exception as e:  # no network, GitHub rate limit…
        log.info("market check failed: %s", e)
        return RETRY_NO_NET
    seen = ",".join(sorted(f"{c['id']}@{c['version']}" for c in cards if c["change"] == "update" and c["compatible"]))
    if seen != cfg.get("market_last_notified", ""):
        if seen:
            try:
                await steam.notify("Module updates available", "There are new versions in the Market.")
            except (Unavailable, InvalidArgument) as e:
                log.info("market notification not shown yet: %s", e)
                return RETRY_NO_STEAM
        config.save(cfg, market_last_notified=seen)
    return CHECK_EVERY
