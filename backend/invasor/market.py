"""The module market (⚙ Settings › Install module › Open the Market).

The repositories listed in the workspace's repos.conf (GitHub, organisation Gamemode-Invasor) are
module repositories: each publishes <id>-<version>.zip and its .sha256 as a GitHub release. catalog()
turns each repository's newest release into a card (name, version, author, description) read from the
module.json *at the release's tag*, never from the downloaded zip: showing the market runs no module
code. install() downloads that zip, verifies its sha256 and hands it to the same install.install that
installs a zip chosen by hand, which checks everything again.

Where the catalog comes from: first market.json, a file a GitHub Action of the workspace repository
regenerates every hour (tools/gen_market.py) and publishes on its market-data branch: one request instead
of about sixteen. It carries no URLs: they are rebuilt here, inside the organisation's releases. A missing,
stale or invalid file falls back to asking GitHub directly, repository by repository.

The client only ever names a repository from the catalog: URLs are built here and must stay inside
that repository's releases. As for the updater, the sha256 catches a corrupt or truncated download,
not a compromised GitHub account.
"""
import hashlib
import hmac
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, config, install, schema, updater
from .schema import InvalidArgument, SchemaError, Unavailable
from .version import parse_version

log = logging.getLogger("invasor.market")

ORG = "Gamemode-Invasor"
CATALOG_URL = f"https://raw.githubusercontent.com/{ORG}/invasor-workspace/main/repos.conf"
FILE_URL = f"https://raw.githubusercontent.com/{ORG}/invasor-workspace/market-data/market.json"
FILE_SCHEMA = 1
CORE_REPO = "invasor"  # listed in repos.conf, but it is the core, not a module
NO_RELEASE = "has no release yet"
MAX_REPOS = 30
MAX_CONF = 64 << 10
MAX_MANIFEST = 256 << 10
MAX_FILE = 256 << 10
MAX_AGE = timedelta(days=3)  # an older market.json is as good as missing: the Action stopped
NAME_MAX, DESCRIPTION_MAX = 200, 2000
CACHE_SECONDS = 600
WORKERS = 4

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ZIP_RE = re.compile(r"^(?P<id>.+)-(?P<version>\d+\.\d+\.\d+(?:-rc\d+)?)\.zip$")


def parse_repos_conf(text):
    """Repository names of a repos.conf: one per line, # comments and blank lines ignored, the core and
    anything that is not a plain repository name skipped, no more than MAX_REPOS."""
    repos = []
    for line in text.splitlines():
        name = line.split("#", 1)[0].strip()
        if name and name != CORE_REPO and REPO_RE.match(name) and name not in repos:
            repos.append(name)
    return repos[:MAX_REPOS]


def release_urls(repo, tag, mid, version):
    """(zip URL, sha256 URL) of a module release: always inside the repository's own releases."""
    base = f"https://github.com/{ORG}/{repo}/releases/download/{tag}/{mid}-{version}.zip"
    return base, base + ".sha256"


def fetch_text(url, limit=MAX_CONF):
    with updater._opener().open(updater._request(url), timeout=updater.TIMEOUT) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("answer too large")
    return data.decode("utf-8", "replace")


class Market:
    def __init__(self, installed=lambda: {}, channel=lambda: "stable", core_version=__version__,
                 fetch=updater.fetch_json, fetch_text=fetch_text, fetch_file=updater.download):
        self._installed = installed  # {module id: installed version}, asked at each listing
        self._channel = channel
        self._core = core_version
        self._fetch, self._text, self._download = fetch, fetch_text, fetch_file
        self._lock = threading.Lock()
        self._cache = None  # (when, channel, {repo: entry}, [notes], {source, generated})

    # ---------- the catalog ----------

    @staticmethod
    def _release_found(repo, release):
        """(id, version, tag) of the release's module zip, which must come with its .sha256 and have both
        assets at the repository's own release URLs, or None."""
        tag = release.get("tag_name")
        if not parse_version(tag):
            return None
        prefix = f"https://github.com/{ORG}/{repo}/releases/download/{tag}/"
        urls = {}
        for a in release.get("assets") or []:
            if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str):
                if a["browser_download_url"] == prefix + a["name"]:
                    urls[a["name"]] = a["browser_download_url"]
        for name in urls:
            m = ZIP_RE.match(name)
            if m and schema.ID_RE.match(m["id"]) and m["version"] == tag.lstrip("v") and name + ".sha256" in urls:
                return m["id"], m["version"], tag
        return None

    def entry(self, repo, beta):
        """The card of one repository (its newest release, pre-releases too with `beta`), or the reason it
        has none (a string)."""
        base = f"https://api.github.com/repos/{ORG}/{repo}/releases"
        try:
            release = self._fetch(f"{base}?per_page=30" if beta else f"{base}/latest")
        except urllib.error.HTTPError as e:
            return NO_RELEASE if e.code == 404 else f"GitHub answered {e.code}"
        if beta:
            release = updater.Updater._newest(release)
        if not isinstance(release, dict) or release.get("draft") or (release.get("prerelease") and not beta):
            return "has no usable release"
        found = self._release_found(repo, release)
        if found is None:
            return "its release has no module zip with a .sha256"
        mid, version, tag = found
        try:
            raw = json.loads(self._text(f"https://raw.githubusercontent.com/{ORG}/{repo}/{tag}/{mid}/module.json", MAX_MANIFEST))
            manifest = schema.parse_manifest(raw, mid)
        except urllib.error.HTTPError as e:
            return f"module.json answered {e.code}"
        except (SchemaError, ValueError) as e:
            return f"module.json is invalid: {e}"
        if manifest["version"] != version:
            return f"module.json says {manifest['version']}, the release {version}"
        return self._card(repo, mid, version, tag, manifest["name"], manifest["description"],
                          manifest["author_name"], manifest["min_core"])

    @staticmethod
    def _card(repo, mid, version, tag, name, description, author, min_core):
        zip_url, sha_url = release_urls(repo, tag, mid, version)
        return {
            "repo": repo, "id": mid, "name": name, "version": version, "tag": tag,
            "description": description, "author": author, "min_core": min_core,
            "zip": zip_url, "sha256": sha_url,
        }

    def _from_file(self, text, beta, now=None):
        """({repo: card}, [notes], generated) of a market.json. A file that is not usable as a whole raises
        ValueError; a card that is not is skipped with a note. Nothing in it is a URL: the cards' URLs are
        rebuilt from repo, tag, id and version."""
        if len(text) > MAX_FILE:
            raise ValueError("market.json is too large")
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("schema") != FILE_SCHEMA:
            raise ValueError("market.json has an unknown schema")
        try:
            generated = datetime.fromisoformat(str(data.get("generated")).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("market.json has no valid date") from None
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        if (now or datetime.now(timezone.utc)) - generated > MAX_AGE:
            raise ValueError(f"market.json is stale ({data.get('generated')})")
        modules = data.get("modules")
        if not isinstance(modules, list):
            raise ValueError("market.json has no module list")
        entries, notes = {}, []
        for item in modules[:MAX_REPOS]:
            repo = item.get("repo") if isinstance(item, dict) else None
            if not isinstance(repo, str) or not REPO_RE.match(repo) or repo == CORE_REPO:
                continue
            card = item.get("beta" if beta else "stable")
            if card is None:
                notes.append(f"{repo}: {NO_RELEASE}")
                continue
            try:
                entries[repo] = self._file_card(repo, card)
            except (ValueError, TypeError, KeyError) as e:
                notes.append(f"{repo}: market.json entry is invalid ({e})")
        for note in (data.get("skipped") or [])[:MAX_REPOS]:
            if isinstance(note, str) and note not in notes and not note.endswith(NO_RELEASE):
                notes.append(note[:200])
        return entries, notes, generated.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _file_card(self, repo, c):
        if not isinstance(c, dict):
            raise ValueError("not an object")
        mid, version, tag = c.get("id"), c.get("version"), c.get("tag")
        if not isinstance(mid, str) or not schema.ID_RE.match(mid):
            raise ValueError("bad id")
        if not isinstance(tag, str) or not parse_version(tag) or not isinstance(version, str) or version != tag.lstrip("v"):
            raise ValueError("bad version")
        name, description, author, min_core = c.get("name"), c.get("description", ""), c.get("author", ""), c.get("min_core")
        if not isinstance(name, str) or not name.strip() or not isinstance(description, str) or not isinstance(author, str):
            raise ValueError("bad text")
        if min_core is not None and (not isinstance(min_core, str) or min_core.startswith("v") or parse_version(min_core) is None):
            raise ValueError("bad min_core")
        one_line = lambda t, n: " ".join(t.split())[:n]  # noqa: E731
        return self._card(repo, mid, version, tag, one_line(name, NAME_MAX), description[:DESCRIPTION_MAX],
                          one_line(author, schema.AUTHOR_MAX), min_core)

    def _load_file(self, beta):
        entries, notes, generated = self._from_file(self._text(FILE_URL, MAX_FILE), beta)
        log.info("market: catalog from market.json (%s)", generated)
        return entries, notes, {"source": "file", "generated": generated}

    def _load_live(self, beta):
        try:
            repos = parse_repos_conf(self._text(CATALOG_URL))
        except Exception as e:
            raise Unavailable(f"couldn't read the list of modules: {e}") from None

        def one(repo):
            try:
                return self.entry(repo, beta)
            except Exception as e:  # one repository must never take the whole market down
                return str(e) or type(e).__name__

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(one, repos))
        entries, notes = {}, []
        for repo, res in zip(repos, results):
            if isinstance(res, dict):
                entries[repo] = res
            else:
                log.info("market: %s %s", repo, res)
                notes.append(f"{repo}: {res}")
        log.info("market: catalog asked of GitHub directly")
        return entries, notes, {"source": "live", "generated": None}

    def _load(self):
        beta = self._channel() == "beta"
        try:
            found = self._load_file(beta)
        except Exception as e:  # missing, stale, broken…: ask GitHub repository by repository
            log.info("market: no usable market.json (%s)", e)
            found = self._load_live(beta)
        entries, notes, meta = found
        return time.monotonic(), "beta" if beta else "stable", entries, notes, meta

    def _catalog(self, refresh=False):
        with self._lock:
            channel = "beta" if self._channel() == "beta" else "stable"
            if refresh or self._cache is None or self._cache[1] != channel or time.monotonic() - self._cache[0] > CACHE_SECONDS:
                self._cache = self._load()
            return self._cache

    @staticmethod
    def _change(installed, offered):
        """What installing the offered version does: None (nothing installed), "same", "update" or
        "downgrade". Versions that can't be compared count as an update."""
        if installed is None:
            return None
        if installed == offered:
            return "same"
        a, b = parse_version(installed), parse_version(offered)
        return "downgrade" if a is not None and b is not None and a > b else "update"

    def list(self, refresh=False):
        """{modules: [card], notes: [why a repository has no card], source: "file"|"live", generated: when
        market.json was made, or None}; each card says what is installed and whether this Invasor can run it."""
        _, _, entries, notes, meta = self._catalog(refresh)
        have = self._installed()
        cards = []
        for e in entries.values():
            problem = schema.core_problem(e, self._core)
            installed = have.get(e["id"])
            cards.append({
                **{k: e[k] for k in ("repo", "id", "name", "version", "description", "author", "min_core")},
                "installed_version": installed,
                "change": self._change(installed, e["version"]),
                "compatible": problem is None,
                "problem": problem,
            })
        cards.sort(key=lambda c: c["name"].lower())
        return {"modules": cards, "notes": notes, **meta}

    # ---------- installing ----------

    @staticmethod
    def _verify(path, sha_text):
        words = sha_text.split()
        if not words or not updater.SHA_RE.match(words[0]):
            raise InvalidArgument("the module's checksum file is unreadable")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if not hmac.compare_digest(h.hexdigest(), words[0].lower()):
            raise InvalidArgument("the module's checksum doesn't match: not installing it")

    def download(self, repo):
        """Download and verify the catalog's zip of `repo`. Returns (path relative to home, the work
        folder to delete afterwards, the module id). The repository must be in the catalog: nothing the client sends
        becomes a URL."""
        entry = self._catalog()[2].get(repo) if isinstance(repo, str) else None
        if entry is None:
            raise InvalidArgument(f"{repo!r} isn't in the market")
        problem = schema.core_problem(entry, self._core)
        if problem:
            raise InvalidArgument(f"{entry['name']} {problem}")
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="market-", dir=config.CACHE_DIR))
        try:
            zip_path = work / f"{entry['id']}-{entry['version']}.zip"
            try:
                self._download(entry["zip"], zip_path, install.MAX_ZIP_BYTES)
                self._download(entry["sha256"], work / "sha256", 1024)
            except Exception as e:
                raise Unavailable(f"couldn't download {entry['name']}: {e}") from None
            self._verify(zip_path, (work / "sha256").read_text(errors="replace"))
            try:
                relative = zip_path.resolve().relative_to(install.HOME.resolve())
            except ValueError:
                raise Unavailable("the cache folder isn't inside your home folder") from None
            return str(relative), work, entry["id"]
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
