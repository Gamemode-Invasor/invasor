"""Tiny local HTTP API (stdlib asyncio) the overlay uses to reach Python features."""
import asyncio
import hmac
import inspect
import json
import logging

from . import modpool
from .schema import InvalidArgument, Unavailable

log = logging.getLogger("invasor.server")

MAX_BODY = 1 << 20
READ_TIMEOUT = 10  # seconds a client gets to send its request
REASONS = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error", 503: "Service Unavailable"}


class ApiServer:
    def __init__(self, cfg, features):
        self.cfg = cfg
        self.features = features

    async def run(self):
        server = await asyncio.start_server(self._client, "127.0.0.1", self.cfg["api_port"])
        log.info("API on 127.0.0.1:%s", self.cfg["api_port"])
        async with server:
            await server.serve_forever()

    async def _client(self, reader, writer):
        try:
            status, body = await self._handle(reader)
            # A body may come already encoded (see _handle); everything else is JSON here.
            payload = b"" if status == 204 else body if isinstance(body, bytes) else json.dumps(body).encode()
        except Exception as e:
            log.exception("request failed")
            status, payload = 500, json.dumps({"error": str(e)}).encode()
        writer.write(
            (
                f"HTTP/1.1 {status} {REASONS.get(status, '')}\r\n"
                "Content-Type: application/json\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                "Access-Control-Allow-Headers: Content-Type, X-Invasor-Token\r\n"
                "Access-Control-Allow-Private-Network: true\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + payload
        )
        try:
            await writer.drain()
        except ConnectionError:
            pass  # the page went away (reload, window closed) before reading the answer
        finally:
            writer.close()

    async def _read_request(self, reader):
        """(method, path, headers, raw body). Time-limited: a stalled client can't hang around."""
        request_line = (await reader.readline()).decode(errors="replace").split()
        if len(request_line) < 2:
            raise ValueError("bad request")
        headers = {}
        while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
            k, _, v = line.decode(errors="replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        body = await reader.readexactly(length) if length else b""
        return request_line[0], request_line[1], headers, body

    async def _handle(self, reader):
        try:
            method, path, headers, raw = await asyncio.wait_for(self._read_request(reader), READ_TIMEOUT)
        except asyncio.TimeoutError:
            return 400, {"error": "request timeout"}
        except (ValueError, asyncio.IncompleteReadError) as e:
            return 400, {"error": str(e) or "bad request"}

        if method == "OPTIONS":
            return 204, None
        if method == "GET" and path == "/health":
            return 200, {"ok": True}
        if not hmac.compare_digest(headers.get("x-invasor-token", "").encode(), self.cfg["token"].encode()):
            return 403, {"error": "bad token"}

        parts = path.strip("/").split("/")
        if method != "POST" or len(parts) != 3 or parts[0] != "api":
            return 404, {"error": "not found"}
        fn = self.features.get(parts[1], {}).get(parts[2])
        if fn is None:
            return 404, {"error": f"unknown method {parts[1]}.{parts[2]}"}

        try:
            args = json.loads(raw) if raw else {}
        except ValueError:
            return 400, {"error": "body is not valid JSON"}
        if not isinstance(args, dict):
            return 400, {"error": "body must be a JSON object"}

        name = f"{parts[1]}.{parts[2]}"
        try:
            inspect.signature(fn).bind(**args)
        except TypeError as e:
            return 400, {"error": f"bad arguments for {name}: {e}"}
        except ValueError:
            pass  # no introspectable signature: let the call itself complain

        # No timeout here: module methods may legitimately take long (downloads, etc.).
        # Plain functions run in modpool's daemon threads, not asyncio's shared executor:
        # a slow one never freezes the event loop (injection, the gamepad combo and the
        # game context keep running), never queues the service's own work behind it, and
        # never keeps the process from stopping.
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                # The panel's own calls (core) skip the module pool: they must answer even
                # when module calls are filling it. What module code they run is time-limited.
                run = asyncio.to_thread if parts[1] == "core" else modpool.run
                result = await run(fn, **args)
                if inspect.isawaitable(result):
                    result = await result
        except InvalidArgument as e:
            log.warning("%s rejected: %s", name, e)
            return 400, {"error": str(e)}
        except Unavailable as e:
            log.warning("%s unavailable: %s", name, e)
            return 503, {"error": str(e)}
        except (Exception, SystemExit) as e:  # a module calling exit() must not stop the service
            log.exception("%s failed", name)
            return 500, {"error": str(e) or type(e).__name__}
        try:
            return 200, json.dumps({"result": result}).encode()
        except (TypeError, ValueError) as e:
            log.error("%s returned something that isn't JSON: %s", name, e)
            return 500, {"error": f"{name} returned a value that can't be sent as JSON ({e})"}
