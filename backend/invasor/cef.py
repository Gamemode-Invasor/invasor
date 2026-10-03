"""Minimal Chrome DevTools Protocol client over a stdlib-only WebSocket.

Kept dependency-free on purpose: system updates can't break what isn't installed.
"""
import asyncio
import base64
import itertools
import json
import logging
import os
import struct
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("invasor.cef")

CEF_HOST = "127.0.0.1"
CEF_PORT = 8080  # STEAM TOUCHPOINT: Steam's CEF DevTools port (enabled by .cef-enable-remote-debugging)
CONNECT_TIMEOUT = 5  # a window that accepts the connection but never answers mustn't hang anyone


def _list_targets_sync():
    url = f"http://{CEF_HOST}:{CEF_PORT}/json"
    with urllib.request.urlopen(url, timeout=3) as resp:
        return json.load(resp)


async def list_targets():
    """Return CEF targets (dicts with an "id"), or [] if Steam/CEF debugging isn't
    reachable. Anything malformed is dropped here, so callers can trust the shape."""
    try:
        targets = await asyncio.to_thread(_list_targets_sync)
    except Exception:
        return []
    if not isinstance(targets, list):
        return []
    return [t for t in targets if isinstance(t, dict) and isinstance(t.get("id"), str)]


def _version_sync():
    # STEAM TOUCHPOINT: /json/version's browser WebSocket URL ends in an id new for each Steam process.
    url = f"http://{CEF_HOST}:{CEF_PORT}/json/version"
    with urllib.request.urlopen(url, timeout=3) as resp:
        return json.load(resp)


async def browser_id():
    """The id of Steam's CEF browser process (the GUID in /json/version's
    webSocketDebuggerUrl), new every time Steam starts; None if it isn't reachable."""
    try:
        info = await asyncio.to_thread(_version_sync)
    except Exception:
        return None
    url = info.get("webSocketDebuggerUrl") if isinstance(info, dict) else None
    if not isinstance(url, str) or "/devtools/browser/" not in url:
        return None
    return url.rsplit("/", 1)[-1] or None


def _mask(data, key):
    """XOR `data` with the 4-byte WebSocket mask `key`. Done on big integers, not byte by
    byte: a large message (an image sent to SteamClient) takes milliseconds, not minutes."""
    n = len(data)
    if not n:
        return b""
    stream = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(n, "big")


class CDPError(Exception):
    pass


class CDPSession:
    """One WebSocket connection to one CEF target."""

    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer
        self._ids = itertools.count(1)
        self._pending = {}
        self._listeners = []
        self._closed = asyncio.Event()
        self._task = asyncio.create_task(self._read_loop())

    @classmethod
    async def connect(cls, ws_url, timeout=CONNECT_TIMEOUT):
        """Open the WebSocket (TCP + handshake within `timeout` seconds, else CDPError)."""
        try:
            return await asyncio.wait_for(cls._connect(ws_url), timeout)
        except asyncio.TimeoutError:
            raise CDPError(f"no answer from {ws_url} in {timeout}s") from None

    @classmethod
    async def _connect(cls, ws_url):
        u = urlparse(ws_url)
        reader, writer = await asyncio.open_connection(u.hostname, u.port or 80)
        try:
            return await cls._handshake(u, reader, writer)
        except BaseException:  # a timeout (cancelled) or a bad answer: don't leave the socket open
            writer.close()
            raise

    @classmethod
    async def _handshake(cls, u, reader, writer):
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(
            (
                f"GET {u.path} HTTP/1.1\r\n"
                f"Host: {u.hostname}:{u.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        status = await reader.readline()
        if b" 101 " not in status:
            raise CDPError(f"websocket handshake failed: {status!r}")
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        return cls(reader, writer)

    @property
    def closed(self):
        return self._closed.is_set()

    async def wait_closed(self):
        await self._closed.wait()

    def on_event(self, callback):
        """callback(method, params) for every CDP event."""
        self._listeners.append(callback)

    async def send(self, method, params=None, timeout=15):
        if self.closed:
            raise CDPError("session closed")
        msg_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        self._send_frame(json.dumps({"id": msg_id, "method": method, "params": params or {}}).encode())
        await self._writer.drain()
        try:
            resp = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(msg_id, None)
        if "error" in resp:
            raise CDPError(resp["error"])
        return resp.get("result", {})

    async def evaluate(self, expression, timeout=15):
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            timeout=timeout,
        )
        if "exceptionDetails" in res:
            raise CDPError(res["exceptionDetails"].get("exception", {}).get("description", res["exceptionDetails"]))
        return res.get("result", {}).get("value")

    async def close(self):
        if not self.closed:
            try:
                self._send_frame(b"", opcode=0x8)
                self._writer.close()
            except Exception:
                pass
        self._task.cancel()
        self._closed.set()

    # --- framing ---

    def _send_frame(self, payload, opcode=0x1):
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = _mask(payload, mask)
        self._writer.write(bytes(header) + mask + masked)

    async def _read_frame(self):
        b1, b2 = await self._reader.readexactly(2)
        opcode, n = b1 & 0x0F, b2 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", await self._reader.readexactly(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", await self._reader.readexactly(8))
        mask = await self._reader.readexactly(4) if b2 & 0x80 else None
        data = await self._reader.readexactly(n)
        if mask:
            data = _mask(data, mask)
        return bool(b1 & 0x80), opcode, data

    async def _read_loop(self):
        buf = b""
        try:
            while True:
                fin, opcode, data = await self._read_frame()
                if opcode == 0x8:
                    break
                if opcode == 0x9:  # ping
                    self._send_frame(data, opcode=0xA)
                    continue
                if opcode in (0x0, 0x1, 0x2):
                    buf += data
                    if not fin:
                        continue
                    msg, buf = json.loads(buf), b""
                    self._dispatch(msg)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("CDP read loop crashed")
        finally:
            self._closed.set()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CDPError("session closed"))

    def _dispatch(self, msg):
        if "id" in msg:
            fut = self._pending.get(msg["id"])
            if fut and not fut.done():
                fut.set_result(msg)
            return
        for cb in self._listeners:
            try:
                cb(msg.get("method"), msg.get("params", {}))
            except Exception:
                log.exception("CDP listener failed")
