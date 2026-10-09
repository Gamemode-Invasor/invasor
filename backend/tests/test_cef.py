import asyncio
import os
import unittest
from unittest import mock

from invasor import cef


def slow_mask(data, key):
    return bytes(b ^ key[i % 4] for i, b in enumerate(data))


class Mask(unittest.TestCase):
    def test_same_as_the_byte_by_byte_definition(self):
        key = os.urandom(4)
        for n in (0, 1, 3, 4, 5, 125, 126, 65535, 65537):
            data = os.urandom(n)
            with self.subTest(n=n):
                self.assertEqual(cef._mask(data, key), slow_mask(data, key))

    def test_round_trip_of_a_big_message(self):
        key, data = os.urandom(4), os.urandom(40 << 20)
        self.assertEqual(cef._mask(cef._mask(data, key), key), data)


if __name__ == "__main__":
    unittest.main()


class Connect(unittest.IsolatedAsyncioTestCase):
    async def test_a_window_that_never_answers_times_out(self):
        """TCP accepted but no handshake: an error in `timeout` seconds, never a hang."""
        done = asyncio.Event()

        async def silent(reader, writer):
            await done.wait()  # accepts, never answers
            writer.close()

        server = await asyncio.start_server(silent, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            try:
                with self.assertRaisesRegex(cef.CDPError, "no answer"):
                    await cef.CDPSession.connect(f"ws://127.0.0.1:{port}/devtools/page/x", timeout=0.2)
            finally:
                done.set()


class Send(unittest.IsolatedAsyncioTestCase):
    async def test_a_window_that_stopped_reading_times_out_in_the_write_too(self):
        """drain() waits when the peer isn't reading and the message is bigger than the buffer."""
        class Stuck:
            def write(self, data):
                pass

            async def drain(self):
                await asyncio.Event().wait()

            def close(self):
                pass

        session = cef.CDPSession(asyncio.StreamReader(), Stuck())
        self.addCleanup(session._task.cancel)
        started = asyncio.get_running_loop().time()
        with self.assertRaises(asyncio.TimeoutError):
            await session.send("Runtime.evaluate", timeout=0.2)
        self.assertLess(asyncio.get_running_loop().time() - started, 1)
        self.assertEqual(session._pending, {})
