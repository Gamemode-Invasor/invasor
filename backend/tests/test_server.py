import asyncio
import json
import time
import unittest

from invasor.schema import InvalidArgument, Unavailable
from invasor.server import ApiServer


class Server(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def slow():
            await asyncio.sleep(0.2)
            return "done"

        def blocking():
            time.sleep(0.3)
            return "slept"

        def two(a, b=1):
            return a + b

        self.cfg = {"api_port": 0, "token": "secret"}
        def offline():
            raise Unavailable("no network")

        def quits():
            raise SystemExit(3)

        def picky(value):
            raise InvalidArgument(f"bad value {value!r}")

        features = {"mod": {
            "picky": picky, "quits": quits, "offline": offline,
            "echo": lambda **kw: kw, "slow": slow, "boom": lambda: 1 / 0,
            "blocking": blocking, "two": two, "object": lambda: object(),
        }}
        api = ApiServer(self.cfg, features)
        self.server = await asyncio.start_server(api._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def request(self, method, path, body=None, token="secret"):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        data = json.dumps(body).encode() if body is not None else b""
        head = f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(data)}\r\n"
        if token:
            head += f"X-Invasor-Token: {token}\r\n"
        writer.write(head.encode() + b"\r\n" + data)
        raw = await reader.read()
        writer.close()
        status = int(raw.split(b" ", 2)[1])
        payload = raw.split(b"\r\n\r\n", 1)[1]
        return status, json.loads(payload) if payload else None

    async def test_token_required(self):
        self.assertEqual((await self.request("POST", "/api/mod/echo", {}, token=None))[0], 403)
        self.assertEqual((await self.request("POST", "/api/mod/echo", {}, token="wrong"))[0], 403)

    async def test_call_and_errors(self):
        self.assertEqual(await self.request("POST", "/api/mod/echo", {"a": 1}), (200, {"result": {"a": 1}}))
        self.assertEqual((await self.request("POST", "/api/mod/nope", {}))[0], 404)
        self.assertEqual((await self.request("POST", "/api/other/echo", {}))[0], 404)
        self.assertEqual((await self.request("POST", "/api/mod/echo", [1]))[0], 400)
        with self.assertLogs("invasor.server", "ERROR"):
            self.assertEqual((await self.request("POST", "/api/mod/boom", {}))[0], 500)

    async def test_async_methods_and_cors(self):
        self.assertEqual(await self.request("POST", "/api/mod/slow", {}), (200, {"result": "done"}))
        self.assertEqual((await self.request("OPTIONS", "/api/mod/echo", token=None))[0], 204)
        self.assertEqual(await self.request("GET", "/health", token=None), (200, {"ok": True}))

    async def test_sync_methods_dont_block_the_loop(self):
        started = time.monotonic()
        slow = asyncio.create_task(self.request("POST", "/api/mod/blocking", {}))
        await asyncio.sleep(0.05)
        # While the blocking method sleeps in its thread, other requests are answered.
        self.assertEqual((await self.request("GET", "/health", token=None))[0], 200)
        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(await slow, (200, {"result": "slept"}))

    async def test_bad_arguments_are_400(self):
        self.assertEqual(await self.request("POST", "/api/mod/two", {"a": 1}), (200, {"result": 2}))
        status, body = await self.request("POST", "/api/mod/two", {"c": 1})
        self.assertEqual(status, 400)
        self.assertIn("bad arguments for mod.two", body["error"])

    async def test_invalid_argument_is_400_without_traceback(self):
        with self.assertLogs("invasor.server", "WARNING") as logs:
            self.assertEqual(await self.request("POST", "/api/mod/picky", {"value": 1}), (400, {"error": "bad value 1"}))
        self.assertTrue(all("Traceback" not in line for line in logs.output))

    async def test_unavailable_is_503(self):
        with self.assertLogs("invasor.server", "WARNING"):
            self.assertEqual(await self.request("POST", "/api/mod/offline", {}), (503, {"error": "no network"}))

    async def test_exit_in_a_method_doesnt_stop_the_server(self):
        with self.assertLogs("invasor.server", "ERROR"):
            self.assertEqual((await self.request("POST", "/api/mod/quits", {}))[0], 500)
        self.assertEqual((await self.request("GET", "/health", token=None))[0], 200)

    async def test_non_json_result_is_a_clean_500(self):
        with self.assertLogs("invasor.server", "ERROR"):
            status, body = await self.request("POST", "/api/mod/object", {})
        self.assertEqual(status, 500)
        self.assertIn("can't be sent as JSON", body["error"])

    async def test_stalled_client_times_out(self):
        import invasor.server as srv
        old, srv.READ_TIMEOUT = srv.READ_TIMEOUT, 0.2
        self.addCleanup(setattr, srv, "READ_TIMEOUT", old)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"POST /api/mod/echo HTTP/1.1\r\n")  # never finishes the headers
        raw = await asyncio.wait_for(reader.read(), 2)
        writer.close()
        self.assertIn(b" 400 ", raw.split(b"\r\n")[0])


if __name__ == "__main__":
    unittest.main()
