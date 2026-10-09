import asyncio
import threading
import time
import unittest

from invasor import modpool
from invasor.schema import Unavailable

from .helpers import patch


class Pool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pool = modpool.ModulePool()
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)

    async def test_result_and_errors_come_back(self):
        self.assertEqual(await self.pool.run(lambda a, b=0: a + b, 1, b=2), 3)
        with self.assertRaises(ZeroDivisionError):
            await self.pool.run(lambda: 1 / 0)
        with self.assertRaises(SystemExit):  # the caller decides what exit() means
            await self.pool.run(lambda: (_ for _ in ()).throw(SystemExit(1)))

    async def test_slow_module_calls_do_not_delay_the_default_executor(self):
        patch(self, "invasor.modpool.MAX_RUNNING", 2)
        calls = [asyncio.create_task(self.pool.run(self.gate.wait, 5)) for _ in range(10)]
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await asyncio.to_thread(lambda: None)  # what the injector and the game poll use
        self.assertLess(time.monotonic() - started, 0.5)
        self.gate.set()
        await asyncio.gather(*calls)

    async def test_a_flood_is_refused_rather_than_queued_for_ever(self):
        patch(self, "invasor.modpool.MAX_RUNNING", 1)
        patch(self, "invasor.modpool.MAX_WAITING", 2)
        calls = [asyncio.create_task(self.pool.run(self.gate.wait, 5)) for _ in range(3)]  # 1 running, 2 waiting
        await asyncio.sleep(0.05)
        with self.assertRaises(Unavailable):
            await self.pool.run(lambda: None)
        self.gate.set()
        await asyncio.gather(*calls)
        self.assertEqual(await self.pool.run(lambda: "again"), "again")  # slots came back

    async def test_its_threads_never_keep_the_process_alive(self):
        task = asyncio.create_task(self.pool.run(self.gate.wait, 5))
        await asyncio.sleep(0.05)
        worker = next(t for t in threading.enumerate() if t.name == "module-call")
        self.assertTrue(worker.daemon)
        self.gate.set()
        await task


if __name__ == "__main__":
    unittest.main()
