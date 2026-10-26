"""http.py: one place to download (the core's own downloads and a module's ctx.http).

No real network: urllib's HTTPS handler is replaced by a fake that answers from a table,
so the core's own opener (and its redirect handler) still does everything else."""
import asyncio
import email.message
import hashlib
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import urllib.response
from unittest import mock

from invasor import http
from invasor.context import GameContext
from invasor.schema import InvalidArgument, Unavailable
from invasor.tasks import TaskRegistry

from .helpers import patch, temp_dir

HOST = "files.example.org"
URL = f"https://{HOST}/pack/thing.zip"
BODY = b"the file's bytes " * 1000
SHA256 = hashlib.sha256(BODY).hexdigest()
SHA512 = hashlib.sha512(BODY).hexdigest()


class FakeNet:
    """urllib's handlers, answering from `routes`: url -> bytes (200), (code, headers, body),
    or an exception to raise. Every URL opened is in `opened`; plain http/ftp in `insecure`."""

    def __init__(self, test):
        self.routes = {}
        self.opened = []
        self.insecure = []
        net = self

        def https_open(handler, req):
            return net._answer(req)

        def insecure_open(handler, req):
            net.insecure.append(req.full_url)
            raise urllib.error.URLError("the test opened an insecure URL")

        for target, value in (("urllib.request.HTTPSHandler.https_open", https_open),
                              ("urllib.request.HTTPHandler.http_open", insecure_open),
                              ("urllib.request.FTPHandler.ftp_open", insecure_open)):
            p = mock.patch(target, value)
            p.start()
            test.addCleanup(p.stop)

    def _answer(self, req):
        url = req.full_url
        self.opened.append(url)
        route = self.routes.get(url)
        if route is None:
            route = (404, {}, b"not here")
        if isinstance(route, BaseException):
            raise route
        if isinstance(route, bytes):
            route = (200, {"Content-Length": str(len(route))}, route)
        code, headers, body = route
        msg = email.message.Message()
        for key, value in headers.items():
            msg[key] = value
        resp = urllib.response.addinfourl(io.BytesIO(body), msg, url, code)
        resp.msg = "OK" if code == 200 else "Whatever"
        return resp


def leftovers(folder, *keep):
    """Files in `folder` other than `keep` (a temp file a failure forgot)."""
    return sorted(p.name for p in folder.iterdir() if p.name not in keep)


# --- the building blocks -------------------------------------------------------------------------

class OpenUrl(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet(self)

    def test_only_https(self):
        for bad in ("http://x.example/a", "file:///etc/passwd", "ftp://x.example/a", "javascript:alert(1)", "x.example/a"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                http.open_url(bad)
        self.assertEqual((self.net.opened, self.net.insecure), ([], []))

    def test_an_https_answer_is_read(self):
        self.net.routes[URL] = b"hello"
        with http.open_url(URL) as resp:
            self.assertEqual(resp.read(), b"hello")

    def test_it_says_who_asks(self):
        seen = []

        def https_open(handler, req):
            seen.append(req.get_header("User-agent"))
            return self.net._answer(req)
        self.net.routes[URL] = b"x"
        with mock.patch("urllib.request.HTTPSHandler.https_open", https_open):
            http.open_url(URL).close()
        self.assertEqual(seen, [http.USER_AGENT])
        self.assertTrue(http.USER_AGENT.startswith("invasor/"))

    def test_a_redirect_that_stays_on_https_is_followed(self):
        other = "https://cdn.example.org/real.zip"
        self.net.routes[URL] = (302, {"Location": other}, b"")
        self.net.routes[other] = b"moved"
        with http.open_url(URL) as resp:
            self.assertEqual(resp.read(), b"moved")
        self.assertEqual(self.net.opened, [URL, other])

    def test_a_redirect_out_of_https_is_refused_before_it_is_followed(self):
        for target in ("http://evil.example/x", "ftp://evil.example/x", "file:///etc/passwd"):
            with self.subTest(target=target):
                self.net.routes[URL] = (302, {"Location": target}, b"")
                self.net.insecure.clear()
                with self.assertRaises(Exception) as caught:
                    http.open_url(URL).close()
                if isinstance(caught.exception, urllib.error.HTTPError):
                    caught.exception.close()  # left open, Python warns when it's collected
                self.assertEqual(self.net.insecure, [])
                self.assertEqual([u for u in self.net.opened if u != URL and not u.startswith("https://")], [])

    def test_credentials_follow_a_redirect_only_on_the_same_host(self):
        seen = []

        def https_open(handler, req):
            seen.append((req.full_url, req.get_header("Authorization"), req.get_header("User-agent"),
                         req.get_header("Accept")))
            return self.net._answer(req)
        same, other = f"https://{HOST}/moved.zip", "https://cdn.example.org/real.zip"
        self.net.routes[URL] = (302, {"Location": same}, b"")
        self.net.routes[same] = (302, {"Location": other}, b"")
        self.net.routes[other] = b"here"
        headers = {"Authorization": "Bearer secret", "Accept": "application/octet-stream"}
        with mock.patch("urllib.request.HTTPSHandler.https_open", https_open):
            with http.open_url(URL, headers=headers) as resp:
                self.assertEqual(resp.read(), b"here")
        self.assertEqual(seen, [
            (URL, "Bearer secret", http.USER_AGENT, "application/octet-stream"),
            (same, "Bearer secret", http.USER_AGENT, "application/octet-stream"),  # same host: kept
            (other, None, http.USER_AGENT, "application/octet-stream"),  # another host: dropped, the rest kept
        ])

    def test_the_redirect_handler_itself_refuses(self):
        handlers = [v for v in vars(http).values()
                    if isinstance(v, type) and issubclass(v, urllib.request.HTTPRedirectHandler)
                    and v is not urllib.request.HTTPRedirectHandler]
        self.assertEqual(len(handlers), 1, "http.py should have one redirect handler of its own")
        handler = handlers[0]()
        req = urllib.request.Request(URL)
        for target in ("http://evil.example/x", "ftp://evil.example/x"):
            with self.subTest(target=target):
                headers = email.message.Message()
                headers["Location"] = target
                try:
                    new = handler.redirect_request(req, io.BytesIO(), 302, "Found", headers, target)
                except Exception:
                    continue  # refused: good
                self.assertIsNone(new, "a redirect out of https must not give a new request")
        headers = email.message.Message()
        headers["Location"] = "https://cdn.example.org/x"
        new = handler.redirect_request(req, io.BytesIO(), 302, "Found", headers, "https://cdn.example.org/x")
        self.assertIsNotNone(new)
        self.assertEqual(new.full_url, "https://cdn.example.org/x")


class FetchJson(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet(self)

    def test_json_is_parsed(self):
        self.net.routes[URL] = json.dumps({"a": [1, 2]}).encode()
        self.assertEqual(http.fetch_json(URL), {"a": [1, 2]})

    def test_too_large_and_not_json_are_value_errors(self):
        self.net.routes[URL] = b"[" + b"1," * 600 + b"1]"
        with self.assertRaises(ValueError):
            http.fetch_json(URL, limit=100)
        self.net.routes[URL] = (200, {}, b"[" + b"1," * 600 + b"1]")  # no length announced: still cut
        with self.assertRaises(ValueError):
            http.fetch_json(URL, limit=100)
        self.net.routes[URL] = b"{not json"
        with self.assertRaises(ValueError):
            http.fetch_json(URL)
        self.net.routes[URL] = b"\xff\xfe\x00garbage"
        with self.assertRaises(ValueError):
            http.fetch_json(URL)


class Save(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet(self)
        self.dir = temp_dir(self)
        self.dest = self.dir / "thing.zip"

    def test_it_saves_and_hashes_while_writing(self):
        self.net.routes[URL] = BODY
        got = http.save(URL, self.dest, limit=len(BODY))
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(got["size"], len(BODY))
        self.assertEqual(got["sha256"], SHA256)
        self.assertEqual(got["sha512"], SHA512)
        self.assertEqual(leftovers(self.dir, "thing.zip"), [])

    def test_not_https_is_a_value_error_and_opens_nothing(self):
        with self.assertRaises(ValueError):
            http.save("http://x.example/a", self.dest, limit=10)
        self.assertEqual((self.net.opened, self.net.insecure), ([], []))
        self.assertFalse(self.dest.exists())

    def test_too_large_leaves_nothing_behind(self):
        for headers in ({"Content-Length": str(len(BODY))}, {}):  # announced, or found out while reading
            with self.subTest(headers=headers):
                self.net.routes[URL] = (200, headers, BODY)
                with self.assertRaises(ValueError):
                    http.save(URL, self.dest, limit=len(BODY) - 1)
                self.assertEqual(leftovers(self.dir), [])

    def test_a_failure_leaves_the_old_file_as_it_was(self):
        self.dest.write_bytes(b"old")
        self.net.routes[URL] = (200, {}, BODY)
        with self.assertRaises(ValueError):
            http.save(URL, self.dest, limit=10)
        self.assertEqual(self.dest.read_bytes(), b"old")
        self.net.routes[URL] = urllib.error.URLError("down")
        with self.assertRaises(OSError):  # URLError is an OSError: it propagates
            http.save(URL, self.dest, limit=len(BODY))
        self.assertEqual(self.dest.read_bytes(), b"old")
        self.assertEqual(leftovers(self.dir, "thing.zip"), [])

    def test_the_old_file_is_replaced_only_at_the_end_from_a_temp_file_beside_it(self):
        self.dest.write_bytes(b"old")
        self.net.routes[URL] = BODY
        during = []

        def progress(done, total):
            during.append((done, total, self.dest.read_bytes(), leftovers(self.dir, "thing.zip")))
        http.save(URL, self.dest, limit=len(BODY), progress=progress)
        self.assertTrue(during)
        self.assertTrue(all(old == b"old" for _, _, old, _ in during))
        self.assertTrue(all(len(temp) == 1 for _, _, _, temp in during), "the temp file is next to dest")
        self.assertEqual(during[-1][:2], (len(BODY), len(BODY)))
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertEqual(leftovers(self.dir, "thing.zip"), [])

    def test_a_stop_cancels_it_and_cleans_up(self):
        big = b"z" * (8 << 20)
        self.net.routes[URL] = big
        stop = threading.Event()
        self.dest.write_bytes(b"old")
        with self.assertRaises(Unavailable):
            http.save(URL, self.dest, limit=len(big), stop=stop, progress=lambda done, total: stop.set())
        self.assertEqual(self.dest.read_bytes(), b"old")
        self.assertEqual(leftovers(self.dir, "thing.zip"), [])

    def test_a_stop_already_set_saves_nothing(self):
        self.net.routes[URL] = BODY
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Unavailable):
            http.save(URL, self.dest, limit=len(BODY), stop=stop)
        self.assertFalse(self.dest.exists())
        self.assertEqual(leftovers(self.dir), [])


# --- ctx.http ------------------------------------------------------------------------------------

class Bridge:
    def __init__(self):
        self.shown = []

    def notify_sync(self, title, body="", icon="", timeout=15, sound=""):
        self.shown.append((title, body))


class CtxHttpBase(unittest.TestCase):
    def setUp(self):
        root = temp_dir(self)
        patch(self, "invasor.modules.DATA_DIR", root / "data")
        self.dir = root / "out"
        self.dir.mkdir()
        self.dest = self.dir / "thing.zip"
        self.net = FakeNet(self)
        self.net.routes[URL] = BODY
        self.tasks = TaskRegistry()
        self.bridge = Bridge()
        from invasor.modules import ModuleContext
        self.ctx = ModuleContext("good", root / "modules" / "good", GameContext(), None, steam=self.bridge, tasks=self.tasks)

    def run_answering(self, fn, answer=None, timeout=10):
        """Run fn() in a thread like a module would, answering any question with `answer`
        (None: a question wasn't expected; it's denied so the call ends). (result, error, questions)."""
        box = {}

        def go():
            try:
                box["value"] = fn()
            except BaseException as e:
                box["error"] = e
        worker = threading.Thread(target=go, daemon=True)
        worker.start()
        questions, answered = [], set()
        deadline = time.monotonic() + timeout
        while worker.is_alive() and time.monotonic() < deadline:
            for view in self.tasks.snapshot()["active"]:
                if view["state"] == "waiting" and view["id"] not in answered:
                    answered.add(view["id"])
                    questions.append(view)
                    self.assertTrue(self.tasks.answer(view["id"], bool(answer)))
            time.sleep(0.005)
        worker.join(0.1)
        self.assertFalse(worker.is_alive(), "the call never ended")
        return box.get("value"), box.get("error"), questions

    def kill(self):
        """What unloading the module does to its context."""
        self.ctx._dead = True
        events = [v for v in vars(self.ctx).values() if isinstance(v, threading.Event)]
        self.assertEqual(len(events), 1, "ModuleContext should have one stop Event")
        events[0].set()

    def wait_notify_threads(self):
        deadline = time.monotonic() + 3
        while any(t.name.startswith("invasor-") and "notif" in t.name for t in threading.enumerate()) and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)


class CtxDownloadArguments(CtxHttpBase):
    def test_bad_urls_never_reach_the_network(self):
        for bad in ("http://x.example/a", "file:///etc/passwd", "ftp://x.example/a", "javascript:alert(1)",
                    "x.example/a", "", None, 5, b"https://x.example/a", "https://x.example/" + "a" * 2048,
                    "https://x.example/a\nb", "https://[abc/file", "https://x.example/\x00", "https://x.example/\x7f"):
            with self.subTest(url=bad):
                with self.assertRaises(InvalidArgument):
                    self.ctx.http.download(bad, self.dest, checksum=("sha256", SHA256))
                with self.assertRaises(InvalidArgument):
                    self.ctx.http.get_json(bad)
        self.assertEqual((self.net.opened, self.net.insecure), ([], []))
        self.assertEqual(self.tasks.snapshot(), {"active": [], "recent": []})

    def test_a_url_of_2048_is_fine(self):
        url = "https://x.example/" + "a" * (2048 - len("https://x.example/"))
        self.net.routes[url] = BODY
        self.ctx.http.download(url, self.dest, checksum=("sha256", SHA256))
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_dest_needs_a_folder_that_exists(self):
        with self.assertRaises(InvalidArgument):
            self.ctx.http.download(URL, self.dir / "nope" / "thing.zip", checksum=("sha256", SHA256))
        self.assertEqual(self.net.opened, [])

    def test_a_malformed_checksum_is_refused_before_any_network(self):
        for bad in (("md5", hashlib.md5(BODY).hexdigest()), ("sha256", SHA256[:-1]), ("sha256", SHA512),
                    ("sha512", SHA256), ("sha256", "g" * 64), ("sha256", SHA256 + "0"), "sha256:" + SHA256,
                    ("sha256",), ("sha256", SHA256, "extra"), (None, SHA256), ("sha256", 123), {"sha256": SHA256},
                    ("sha256", " " + SHA256[1:]), ("sha256", b"0" * 64), SHA256, 7, True):
            with self.subTest(checksum=bad), self.assertRaises(InvalidArgument):
                self.ctx.http.download(URL, self.dest, checksum=bad)
        self.assertEqual(self.net.opened, [])
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.tasks.snapshot()["active"], [])

    def test_max_bytes_over_100_mb_with_user_chose(self):
        with self.assertRaises(InvalidArgument):
            self.ctx.http.download(URL, self.dest, user_chose=True, max_bytes=(100 << 20) + (1 << 20))
        self.assertEqual(self.net.opened, [])


class CtxDownload(CtxHttpBase):
    def test_a_good_checksum_saves_it_without_asking(self):
        for checksum in (("sha256", SHA256), ["sha256", SHA256], ("sha512", SHA512), ("sha256", SHA256.upper()),
                         ("sha512", SHA512.upper())):
            with self.subTest(checksum=checksum):
                value, error, questions = self.run_answering(
                    lambda: self.ctx.http.download(URL, self.dest, checksum=checksum))
                self.assertIsNone(error)
                self.assertEqual(questions, [])
                self.assertEqual(value, {"path": str(self.dest), "size": len(BODY), "sha256": SHA256, "sha512": SHA512})
                self.assertEqual(self.dest.read_bytes(), BODY)

    def test_the_file_never_has_execute_bits(self):
        self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256))
        self.assertEqual(self.dest.stat().st_mode & 0o111, 0)
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o644)

    def test_an_existing_dest_is_replaced(self):
        self.dest.write_bytes(b"old")
        self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256))
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_a_checksum_that_doesnt_match_leaves_no_file(self):
        wrong = hashlib.sha256(b"other").hexdigest()
        for prior in (None, b"old"):
            with self.subTest(prior=prior):
                if prior is None:
                    self.dest.unlink(missing_ok=True)
                else:
                    self.dest.write_bytes(prior)
                with self.assertRaisesRegex(InvalidArgument, "checksum"):
                    self.ctx.http.download(URL, self.dest, checksum=("sha256", wrong))
                if self.dest.exists():
                    self.assertNotEqual(self.dest.read_bytes(), BODY, "the bad file was left at dest")
                    self.assertEqual(self.dest.read_bytes(), prior)
                self.assertEqual(leftovers(self.dir, "thing.zip"), [])

    def test_no_checksum_asks_the_user_and_accept_downloads(self):
        value, error, questions = self.run_answering(lambda: self.ctx.http.download(URL, self.dest), answer=True)
        self.assertIsNone(error)
        self.assertEqual(len(questions), 1)
        question = questions[0]["question"]
        self.assertIn(HOST, question)
        self.assertIn("good", question)
        self.assertEqual(questions[0]["module"], "good")
        self.assertEqual(value["sha256"], SHA256)
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_the_question_comes_before_any_download(self):
        opened_when_asked = []
        orig = self.tasks.answer

        def answer(task_id, accept):
            opened_when_asked.append(list(self.net.opened))
            return orig(task_id, accept)
        self.tasks.answer = answer
        self.run_answering(lambda: self.ctx.http.download(URL, self.dest), answer=True)
        self.assertEqual(opened_when_asked, [[]])

    def test_deny_downloads_nothing(self):
        value, error, questions = self.run_answering(lambda: self.ctx.http.download(URL, self.dest), answer=False)
        self.assertEqual(len(questions), 1)
        self.assertIsInstance(error, Unavailable)
        self.assertIn("allowed", str(error))
        self.assertEqual(self.net.opened, [])
        self.assertFalse(self.dest.exists())
        self.assertEqual(leftovers(self.dir), [])

    def test_waiting_for_an_answer_tells_steam(self):
        self.run_answering(lambda: self.ctx.http.download(URL, self.dest), answer=False)
        self.wait_notify_threads()
        self.assertTrue(any("good" in title for title, _ in self.bridge.shown), self.bridge.shown)

    def test_a_steam_that_fails_to_notify_is_not_fatal(self):
        def boom(*a, **k):
            raise Unavailable("no steam")
        self.bridge.notify_sync = boom
        value, error, _ = self.run_answering(lambda: self.ctx.http.download(URL, self.dest), answer=True)
        self.assertIsNone(error)
        self.assertEqual(self.dest.read_bytes(), BODY)

    def test_user_chose_doesnt_ask_and_is_logged(self):
        with self.assertLogs(level="INFO") as logs:
            value, error, questions = self.run_answering(
                lambda: self.ctx.http.download(URL, self.dest, user_chose=True))
        self.assertIsNone(error)
        self.assertEqual(questions, [])
        self.assertEqual(self.dest.read_bytes(), BODY)
        self.assertTrue(any("good" in line and HOST in line for line in logs.output), logs.output)

    def test_user_chose_with_a_checksum_still_checks_it(self):
        _, error, questions = self.run_answering(
            lambda: self.ctx.http.download(URL, self.dest, user_chose=True, checksum=("sha256", "0" * 64)))
        self.assertIsInstance(error, InvalidArgument)
        self.assertEqual(questions, [])

    def test_max_bytes_is_kept(self):
        for headers in ({"Content-Length": str(len(BODY))}, {}):
            with self.subTest(headers=headers):
                self.net.routes[URL] = (200, headers, BODY)
                with self.assertRaises(InvalidArgument):
                    self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256), max_bytes=len(BODY) - 1)
                self.assertFalse(self.dest.exists())
                self.assertEqual(leftovers(self.dir), [])

    def test_its_own_task_is_quiet_and_ends(self):
        self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256))
        snap = self.tasks.snapshot()
        self.assertEqual(snap["active"], [])
        self.assertEqual([(t["module"], t["state"]) for t in snap["recent"]], [("good", "done")])
        self.assertIn("thing.zip", snap["recent"][0]["label"])
        self.wait_notify_threads()
        self.assertEqual(self.bridge.shown, [])  # notify=False

    def test_a_given_task_is_used_and_no_other_is_opened(self):
        task = self.ctx.task("Installing a pack", notify=False)
        fractions = []
        self.tasks.watch(lambda: fractions.append(self.tasks.snapshot()["active"][0]["fraction"]
                                                  if self.tasks.snapshot()["active"] else None))
        self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256), task=task)
        snap = self.tasks.snapshot()
        self.assertEqual([t["id"] for t in snap["active"]], [task.id])  # still the caller's to end
        self.assertEqual(snap["recent"], [])
        self.assertTrue(any(f for f in fractions if f), "the progress went to the given task")
        task.done()

    def test_the_question_is_asked_on_the_given_task(self):
        task = self.ctx.task("Installing a pack", notify=False)
        _, error, questions = self.run_answering(lambda: self.ctx.http.download(URL, self.dest, task=task), answer=True)
        self.assertIsNone(error)
        self.assertEqual([q["id"] for q in questions], [task.id])
        self.assertEqual(len(self.tasks.snapshot()["active"]), 1)
        task.done()

    def test_server_errors_are_unavailable(self):
        for route, words in (((404, {}, b"nope"), "404"), ((500, {}, b""), "500"),
                             (urllib.error.URLError("no route"), ""), (OSError("reset"), ""),
                             (TimeoutError("timed out"), "")):
            with self.subTest(route=route):
                self.net.routes[URL] = route
                with self.assertRaisesRegex(Unavailable, words):
                    self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256))
                self.assertFalse(self.dest.exists())
                self.assertEqual(leftovers(self.dir), [])
        self.assertEqual(self.tasks.snapshot()["active"], [])

    def test_a_redirect_out_of_https_is_refused(self):
        self.net.routes[URL] = (302, {"Location": "http://evil.example/x"}, b"")
        with self.assertRaises((InvalidArgument, Unavailable)):
            self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256))
        self.assertEqual(self.net.insecure, [])
        self.assertFalse(self.dest.exists())


class CtxUnloaded(CtxHttpBase):
    def test_after_unload_new_calls_are_unavailable(self):
        self.kill()
        with self.assertRaises(Unavailable):
            self.ctx.http.download(URL, self.dest, checksum=("sha256", SHA256))
        with self.assertRaises(Unavailable):
            self.ctx.http.download(URL, self.dest, user_chose=True)
        with self.assertRaises(Unavailable):
            self.ctx.http.get_json(URL)
        self.assertEqual(self.net.opened, [])

    def test_an_unload_stops_a_running_download(self):
        big = b"z" * (8 << 20)
        self.net.routes[URL] = big
        digest = hashlib.sha256(big).hexdigest()
        killed = []

        def on_change():
            active = self.tasks.snapshot()["active"]
            if not killed and active and active[0]["fraction"]:
                killed.append(1)
                self.kill()
        self.tasks.watch(on_change)
        _, error, _ = self.run_answering(lambda: self.ctx.http.download(URL, self.dest, checksum=("sha256", digest)))
        self.assertTrue(killed, "the download never reported progress")
        self.assertIsInstance(error, Unavailable)
        self.assertFalse(self.dest.exists())
        self.assertEqual(leftovers(self.dir), [])

    def test_an_unload_while_asking_ends_it_denied(self):
        box = {}
        worker = threading.Thread(target=lambda: box.update(
            error=self._catch(lambda: self.ctx.http.download(URL, self.dest))), daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while not any(v["state"] == "waiting" for v in self.tasks.snapshot()["active"]) and time.monotonic() < deadline:
            time.sleep(0.005)
        self.kill()
        self.tasks.close_owner("good", "module unloaded")  # what _forget_context does too
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(box["error"], Unavailable)
        self.assertEqual(self.net.opened, [])

    @staticmethod
    def _catch(fn):
        try:
            fn()
        except BaseException as e:
            return e
        return None

    def test_forgetting_the_context_sets_its_stop_event(self):
        from invasor.modules import ModuleManager
        patch(self, "invasor.modules.MODULES_DIR", self.dir / "m")
        patch(self, "invasor.modules.USER_MODULES_DIR", self.dir / "um")
        manager = ModuleManager({"disabled_modules": []}, GameContext())
        manager._contexts["good"] = self.ctx
        manager._forget_context("good")
        events = [v for v in vars(self.ctx).values() if isinstance(v, threading.Event)]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].is_set())


class CtxGetJson(CtxHttpBase):
    def test_json_comes_back_with_no_task_and_no_question(self):
        self.net.routes[URL] = b'{"ok": true, "n": [1, 2]}'
        value, error, questions = self.run_answering(lambda: self.ctx.http.get_json(URL))
        self.assertIsNone(error)
        self.assertEqual(value, {"ok": True, "n": [1, 2]})
        self.assertEqual(questions, [])
        self.assertEqual(self.tasks.snapshot(), {"active": [], "recent": []})

    def test_too_large_and_bad_json_are_invalid(self):
        self.net.routes[URL] = (200, {}, b"[" + b"1," * 100 + b"1]")
        with self.assertRaises(InvalidArgument):
            self.ctx.http.get_json(URL, max_bytes=50)
        for bad in (b"{not json", b"", b"\xff\xfe"):
            with self.subTest(body=bad):
                self.net.routes[URL] = bad
                with self.assertRaises(InvalidArgument):
                    self.ctx.http.get_json(URL)

    def test_the_default_limit_is_one_mb(self):
        self.net.routes[URL] = (200, {}, b'"' + b"a" * (1 << 20) + b'"')
        with self.assertRaises(InvalidArgument):
            self.ctx.http.get_json(URL)

    def test_errors_are_unavailable(self):
        for route in ((404, {}, b""), urllib.error.URLError("down")):
            with self.subTest(route=route):
                self.net.routes[URL] = route
                with self.assertRaises(Unavailable):
                    self.ctx.http.get_json(URL)


class CtxAsync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CtxHttpBase.setUp(self)

    async def test_the_async_twins(self):
        self.net.routes["https://api.example.org/x.json"] = b'[1, "two"]'
        self.assertEqual(await self.ctx.http.get_json_async("https://api.example.org/x.json"), [1, "two"])
        got = await self.ctx.http.download_async(URL, self.dest, checksum=("sha256", SHA256))
        self.assertEqual(got["sha256"], SHA256)
        self.assertEqual(self.dest.read_bytes(), BODY)
        with self.assertRaises(InvalidArgument):
            await self.ctx.http.download_async("http://x.example/a", self.dest, checksum=("sha256", SHA256))

    async def test_the_loop_keeps_running_while_it_downloads(self):
        ticks = []

        async def ticker():
            for _ in range(3):
                ticks.append(1)
                await asyncio.sleep(0)
        await asyncio.gather(ticker(), self.ctx.http.download_async(URL, self.dest, checksum=("sha256", SHA256)))
        self.assertEqual(len(ticks), 3)


if __name__ == "__main__":
    unittest.main()
