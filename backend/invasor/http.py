"""Downloads over https: the one place the core goes to the network for a file or a JSON answer.

open_url / fetch_json / save are what the updater and the Market use. ModuleHttp is `ctx.http`,
what a module gets: the same, with its arguments checked, and a download that the user must allow
(in the Tasks panel) unless the module knows the file's checksum or the user just chose it.
"""
import asyncio
import contextlib
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .schema import InvalidArgument, Unavailable
from .steam import notify_later
from .tasks import Task

log = logging.getLogger("invasor.http")

USER_AGENT = f"invasor/{__version__}"  # GitHub's API refuses requests without one
TIMEOUT = 20
CHUNK = 1 << 16
MAX_URL = 2048
MAX_DOWNLOAD = 1 << 30
MAX_USER_CHOSE = 100 << 20
MAX_JSON = 1 << 20
HEX = {"sha256": re.compile(r"^[0-9a-fA-F]{64}$"), "sha512": re.compile(r"^[0-9a-fA-F]{128}$")}
# What a request may fail with besides ValueError: no network, the server's answer, a cut transfer.
NET_ERRORS = (OSError, http.client.HTTPException)


def _https(url):
    return urlsplit(url).scheme == "https"


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another https URL: the one that leaves https is refused before
    anything is asked of it. Credentials (Authorization) never go on to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _https(newurl):
            fp.close()
            raise ValueError(f"redirected to a non-https URL: {newurl}")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _HttpsOnly)


def open_url(url, *, timeout=TIMEOUT, headers=None):
    """The answer to a GET of url (https only), to use with `with`."""
    if not isinstance(url, str) or not _https(url):
        raise ValueError(f"not an https URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    r = _opener().open(req, timeout=timeout)
    if not _https(r.geturl()):  # _HttpsOnly already refuses it: this only double-checks
        r.close()
        raise ValueError(f"redirected to a non-https URL: {r.geturl()}")
    return r


def fetch_json(url, *, limit=MAX_JSON, headers=None):
    with open_url(url, headers=headers) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("answer too large")
    return json.loads(data)


def _length(r):
    try:
        return int(r.headers.get("Content-Length") or 0) or None
    except (AttributeError, ValueError):
        return None


def save(url, dest, *, limit, progress=None, stop=None, headers=None, checksum=None):
    """Save url at dest, giving up past `limit` bytes: {"size", "sha256", "sha512"}. It is written
    next to dest and only takes its place once complete (and matching `checksum`, an (algo, hex)
    pair, if given), so a failure leaves dest as it was. `progress(done, total)` hears the bytes in
    so far (total None when the server doesn't say); `stop` (threading.Event) cancels it."""
    dest = Path(dest)
    if stop is not None and stop.is_set():
        raise Unavailable("cancelled")
    with open_url(url, headers=headers) as r:
        total = _length(r)
        fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
        try:
            hashes = {"sha256": hashlib.sha256(), "sha512": hashlib.sha512()}
            size = 0
            with os.fdopen(fd, "wb") as f:
                while chunk := r.read(CHUNK):
                    if stop is not None and stop.is_set():
                        raise Unavailable("cancelled")
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("download too large")
                    f.write(chunk)
                    for h in hashes.values():
                        h.update(chunk)
                    if progress:
                        progress(size, total)
            result = {"size": size, **{name: h.hexdigest() for name, h in hashes.items()}}
            if checksum and not hmac.compare_digest(result[checksum[0]], checksum[1].lower()):
                raise ValueError("the download's checksum doesn't match")
            os.chmod(tmp, 0o644)  # never executable, whatever it holds
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    return result


def _url(url):
    if not isinstance(url, str) or len(url) > MAX_URL or not _https(url) or not urlsplit(url).hostname:
        raise InvalidArgument(f"not an https URL (up to {MAX_URL} characters): {str(url)[:100]!r}")
    return url


def _dest(dest):
    try:
        dest = Path(dest)
    except TypeError:
        raise InvalidArgument("dest must be a path") from None
    if not dest.name or not dest.parent.is_dir() or dest.is_dir():
        raise InvalidArgument(f"dest must be a file in a folder that exists: {str(dest)!r}")
    return dest


def _checksum(checksum):
    if checksum is None:
        return None
    if isinstance(checksum, (tuple, list)) and len(checksum) == 2 and isinstance(checksum[1], str) \
            and checksum[0] in HEX and HEX[checksum[0]].match(checksum[1]):
        return checksum[0], checksum[1].lower()
    raise InvalidArgument('checksum must be ("sha256", 64 hex digits) or ("sha512", 128 hex digits)')


def _limit(max_bytes, user_chose):
    top = MAX_USER_CHOSE if user_chose else MAX_DOWNLOAD
    if max_bytes is None:
        return top
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= top:
        raise InvalidArgument(f"max_bytes must be a whole number from 1 to {top}")
    return max_bytes


def _not_on_loop(name):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise Unavailable(f"ctx.http.{name}() waits for the network: call it from a plain (threaded) method, "
                      f"or use {name}_async() in `async def`")


def _network(what, error):
    if isinstance(error, urllib.error.HTTPError):
        error.close()  # it holds the server's answer open
        return Unavailable(f"couldn't {what}: the server answered {error.code}")
    reason = getattr(error, "reason", None) or getattr(error, "strerror", None) or error
    return Unavailable(f"couldn't {what}: {reason}")


class ModuleHttp:
    """`ctx.http`: downloads for a module. The plain methods block: call them from a plain (threaded)
    method; in `async def`, the `_async` twins."""

    def __init__(self, ctx):
        self._ctx = ctx

    def download(self, url, dest, *, checksum=None, user_chose=False, max_bytes=None, task=None):
        """Save the https `url` at `dest` (a file in a folder that exists; one already there is
        replaced only once the download is complete): {"path", "size", "sha256", "sha512"}.
        - checksum: ("sha256" or "sha512", hex). A file that doesn't match it is not kept
          (InvalidArgument). Without it the user is asked in the Tasks panel first, unless:
        - user_chose=True: the user just picked this very file (an image, say). Capped at 100 MB.
        - max_bytes: the most it may take (1 GiB by default, 100 MB with user_chose).
        - task: a ctx.task() of this module to show the progress and the question in; without
          it the download opens one of its own.
        Unavailable when the user says no, the network fails or the module is unloaded meanwhile.
        The file is never executable."""
        self._check_call("download")
        url, dest = _url(url), _dest(dest)
        checksum, limit = _checksum(checksum), _limit(max_bytes, user_chose)
        if not isinstance(user_chose, bool):
            raise InvalidArgument("user_chose must be True or False")
        if task is not None and (not isinstance(task, Task) or task.owner != self._ctx.id):
            raise InvalidArgument("task must be one of this module's ctx.task()")
        if task is not None:
            return self._download(url, dest, checksum, user_chose, limit, task)
        with self._ctx.task(f"Downloading {dest.name}", notify=False) as own:
            return self._download(url, dest, checksum, user_chose, limit, own)

    def _download(self, url, dest, checksum, user_chose, limit, task):
        host = urlsplit(url).hostname
        if checksum is None and not user_chose:
            self._notify_question()
            if not task.ask(f"Download {dest.name} from {host}? Invasor can't check it is the file {self._ctx.id} expects."):
                raise Unavailable("the download wasn't allowed")
        if user_chose:
            log.info("%s downloads a file the user chose from %s to %s", self._ctx.id, host, dest)
        try:
            res = save(url, dest, limit=limit, checksum=checksum, stop=self._ctx._stopped,
                       progress=task.progress(0, 1, "Downloading"))
        except ValueError as e:
            raise InvalidArgument(str(e)) from None
        except NET_ERRORS as e:
            raise _network("download it", e) from None
        return {"path": str(dest), **res}

    def get_json(self, url, *, max_bytes=MAX_JSON):
        """The JSON answer of the https `url` (at most `max_bytes`). InvalidArgument when it isn't
        JSON or is too large, Unavailable when the network fails."""
        self._check_call("get_json")
        url = _url(url)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_DOWNLOAD:
            raise InvalidArgument(f"max_bytes must be a whole number from 1 to {MAX_DOWNLOAD}")
        try:
            return fetch_json(url, limit=max_bytes)
        except ValueError as e:
            raise InvalidArgument(f"the answer isn't usable JSON: {e}") from None
        except NET_ERRORS as e:
            raise _network("get it", e) from None

    async def download_async(self, url, dest, **options):
        """The same as download(), for `async def` methods."""
        return await asyncio.to_thread(self.download, url, dest, **options)

    async def get_json_async(self, url, **options):
        """The same as get_json(), for `async def` methods."""
        return await asyncio.to_thread(self.get_json, url, **options)

    def _check_call(self, name):
        _not_on_loop(name)
        if self._ctx._dead:
            raise Unavailable("this module was unloaded: it can't download anymore")

    def _notify_question(self):
        # STEAM TOUCHPOINT (see docs/API-Steam.md, 2.3): a toast so the user knows a question waits.
        # A thread of its own: notify_sync() waits for Steam. Never fatal.
        notify_later(self._ctx._steam, f"{self._ctx.id} needs an answer", "Open Invasor's Tasks panel",
                     self._ctx.log, "question", "invasor-ask-notify")
