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
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, config
from .schema import InvalidArgument, Unavailable
from .storage import JsonStore

log = logging.getLogger("invasor.updater")

REPO = "Gamemode-Invasor/invasor"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
USER_AGENT = f"invasor/{__version__}"  # GitHub's API refuses requests without one
TIMEOUT = 20
MAX_JSON = 1 << 20
MAX_PACKAGE = 64 << 20
MAX_UNPACKED = 256 << 20

STARTUP_DELAY = 60  # let Steam come up: the notification needs it
CHECK_EVERY = 24 * 3600
RETRY_NO_NET = 3600
RETRY_NO_STEAM = 900
WHEN_OFF = 3600  # how often to see whether the user switched checking back on

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
INIT_VERSION_RE = re.compile(r'^__version__ = "(.*)"$', re.M)

# Runs the installer from the unpacked release, then removes the work folder. $1: the
# unpacked release, $2: the work folder. The installer restarts the service, which
# re-injects the new overlay into Steam's windows: Steam itself is never restarted.
UPDATE_SCRIPT = r"""
bash "$1/invasor-installation.sh" --install || echo "invasor update: the installer failed" >&2
rm -rf "$2"
"""


def parse_version(text):
    """(major, minor, patch) of a plain X.Y.Z tag (optional leading v), else None:
    prereleases and anything unusual are never offered."""
    m = VERSION_RE.match(text) if isinstance(text, str) else None
    return tuple(int(g) for g in m.groups()) if m else None


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


def download(url, dest, limit):
    """Save url (https only) at dest, giving up past `limit` bytes."""
    if not url.startswith("https://"):
        raise ValueError(f"not an https URL: {url}")
    size = 0
    with _opener().open(_request(url), timeout=TIMEOUT) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            size += len(chunk)
            if size > limit:
                raise ValueError("download too large")
            f.write(chunk)


def launch(root, workdir):
    """Run the installer from the unpacked release, detached from this service."""
    cmd = ["systemd-run", "--user", "--collect", "--quiet", "--unit=invasor-update",
           "bash", "-c", UPDATE_SCRIPT, "invasor-update", str(root), str(workdir)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise Unavailable("systemd-run not found: can't update from here") from None
    except subprocess.CalledProcessError as e:
        raise Unavailable(f"couldn't start the update ({(e.stderr or '').strip() or e.returncode})") from None


def safe_extract(archive, dest, root_name):
    """Unpack the package under dest/<root_name>: only plain files and folders, nothing
    outside that folder (no absolute paths, .., links or devices), bounded in size."""
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            parts = Path(m.name).parts
            if m.name.startswith("/") or ".." in parts or not parts or parts[0] != root_name:
                raise ValueError(f"unsafe path in the package: {m.name!r}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"unexpected entry in the package: {m.name!r}")
            total += m.size
            if total > MAX_UNPACKED:
                raise ValueError("package too large once unpacked")
        for m in members:
            m.mode = 0o755 if (m.isdir() or m.mode & 0o100) else 0o644
            m.uid = m.gid = 0
            m.uname = m.gname = ""
        tar.extractall(dest, members)
    return Path(dest) / root_name


class Updater:
    def __init__(self, current=__version__, fetch=fetch_json, fetch_file=download, start=launch):
        self.current = current
        self._fetch, self._download, self._start = fetch, fetch_file, start
        self.last = None  # the latest check()'s answer, for the panel to show on opening
        self._package = None  # (package URL, sha256 URL) of that check's release

    def _unknown(self, why):
        log.info("couldn't check for updates: %s", why)
        self.last = {"current": self.current, "latest": None, "available": False, "checked": False, "notes": ""}
        return self.last

    def check(self):
        """{current, latest, available, checked, notes}. Offline, no release yet, a release
        without a package…: checked is False (nothing to report, nothing wrong)."""
        try:
            release = self._fetch(RELEASE_URL)
        except urllib.error.HTTPError as e:
            return self._unknown(f"GitHub answered {e.code}")
        except Exception as e:
            return self._unknown(e)
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            return self._unknown("no usable release")
        latest = parse_version(release.get("tag_name"))
        mine = parse_version(self.current)
        if latest is None or mine is None:
            return self._unknown(f"unreadable version ({release.get('tag_name')!r} / {self.current!r})")
        version = ".".join(map(str, latest))
        package = self._find_package(release, version)
        if latest > mine and package is None:
            return self._unknown(f"release {version} has no package")
        notes = release.get("body")
        self.last = {
            "current": self.current,
            "latest": version,
            "available": latest > mine,
            "checked": True,
            "notes": notes[:2000] if isinstance(notes, str) else "",
        }
        self._package = package
        return self.last

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

    def apply(self):
        """Download, verify and unpack the new version, and hand over to the installer.
        Returns once it's started: the service is restarted by it a moment later."""
        res = self.check()
        if not res["checked"] or not res["available"]:
            raise InvalidArgument("there's no update to install")
        version, (pkg_url, sha_url) = res["latest"], self._package
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="update-", dir=config.CACHE_DIR))
        try:
            tarball = work / f"invasor-{version}.tar.gz"
            try:
                self._download(pkg_url, tarball, MAX_PACKAGE)
                self._download(sha_url, work / "sha256", 1024)
            except Exception as e:
                raise Unavailable(f"couldn't download the update: {e}") from None
            self._verify(tarball, (work / "sha256").read_text(errors="replace"))
            try:
                root = safe_extract(tarball, work, f"invasor-{version}")
            except (ValueError, tarfile.TarError, OSError) as e:
                raise InvalidArgument(f"the update package is invalid: {e}") from None
            self._check_contents(root, version)
            log.info("updating %s -> %s", self.current, version)
            self._start(root, work)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        return {"version": version}

    @staticmethod
    def _verify(tarball, sha_text):
        words = sha_text.split()
        if not words or not SHA_RE.match(words[0]):
            raise InvalidArgument("the update's checksum file is unreadable")
        h = hashlib.sha256()
        with open(tarball, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if not hmac.compare_digest(h.hexdigest(), words[0].lower()):
            raise InvalidArgument("the update's checksum doesn't match: not installing it")

    @staticmethod
    def _check_contents(root, version):
        script = root / "invasor-installation.sh"
        init = root / "backend" / "invasor" / "__init__.py"
        if not script.is_file() or not init.is_file():
            raise InvalidArgument("the update package is incomplete")
        m = INIT_VERSION_RE.search(init.read_text(errors="replace"))
        if not m or m.group(1) != version:
            raise InvalidArgument(f"the package holds version {m.group(1) if m else '?'}, not {version}")


async def run(updater, cfg, steam):
    """Background: look for an update at startup and then daily, and tell Steam about each
    new version once. Switched off by the update_check preference."""
    await asyncio.sleep(STARTUP_DELAY)
    while True:
        delay = CHECK_EVERY
        try:
            delay = await _round(updater, cfg, steam)
        except Exception:
            # Never let this background task end: a service task that stops stops Invasor.
            log.exception("update check failed")
            delay = RETRY_NO_NET
        await asyncio.sleep(delay)


async def _round(updater, cfg, steam):
    """One pass of run(): seconds until the next one."""
    if not cfg.get("update_check", True):
        return WHEN_OFF
    res = await asyncio.to_thread(updater.check)
    if not res["checked"]:
        return RETRY_NO_NET
    if res["available"] and cfg.get("update_last_notified") != res["latest"]:
        try:
            await steam.notify(
                "Invasor update available",
                f"Version {res['latest']} is out (you have {res['current']}). Open Invasor > Settings > Updates.",
            )
        except (Unavailable, InvalidArgument) as e:
            log.info("update notification not shown yet: %s", e)
            return RETRY_NO_STEAM
        cfg["update_last_notified"] = res["latest"]
        JsonStore(config.CONFIG_FILE).update(update_last_notified=res["latest"])
    return CHECK_EVERY
