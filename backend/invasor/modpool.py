"""Where module code runs when the service calls it from the event loop.

Not asyncio's default executor, for two reasons. That one is shared with the service's own
plumbing (CDP target lists, the game poll, Steam calls): a handful of slow module calls
would queue the injection rounds behind them. And its threads are joined when the process
exits, so a module call still running (a download, a hung subprocess) kept SIGTERM waiting
until systemd killed us, before any teardown() ran.

Each call gets a daemon thread of its own (a call is a rare, human-paced event), at most
MAX_RUNNING at a time. Calls beyond that wait, up to MAX_WAITING of them; past that the
caller gets Unavailable (503) instead of an ever-growing queue. A call that never ends
can't hold the service up when it stops, and gives its slot back after SLOT_SECONDS (the
caller keeps waiting for it; the thread is left running), so that eight hung calls
don't leave module calls unavailable until the service restarts.
"""
import asyncio
import logging
import threading

from .schema import Unavailable

MAX_RUNNING = 8
MAX_WAITING = 32
SLOT_SECONDS = 600  # a call may legitimately take long (downloads); past this it no longer holds a slot

log = logging.getLogger("invasor.modpool")


class ModulePool:
    def __init__(self):
        self._sem = None
        self._loop = None
        self._waiting = 0

    def _semaphore(self):
        # Made on first use, and again if the loop changed: on Python 3.9 asyncio's
        # primitives bind to the loop that is running when they are created.
        loop = asyncio.get_running_loop()
        if self._sem is None or self._loop is not loop:
            self._sem, self._loop, self._waiting = asyncio.Semaphore(MAX_RUNNING), loop, 0
        return self._sem

    async def run(self, fn, *args, **kwargs):
        """fn(*args, **kwargs) in its own daemon thread; its result, or what it raised
        (SystemExit included). Unavailable when too many calls are already waiting."""
        sem = self._semaphore()
        if sem.locked() and self._waiting >= MAX_WAITING:
            raise Unavailable("too many module calls in progress, try again in a moment")
        self._waiting += 1
        try:
            await sem.acquire()
        finally:
            self._waiting -= 1
        loop = asyncio.get_running_loop()
        done = loop.create_future()

        def settle(setter, value):
            if not done.done():
                setter(value)

        slot = [True]

        def give_back(late=False):
            if slot[0]:
                slot[0] = False
                sem.release()
                if late:
                    log.warning("a module call is still running after %ss: its slot is free again", SLOT_SECONDS)

        timer = loop.call_later(SLOT_SECONDS, give_back, True)

        def work():
            try:
                result = fn(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001 - handed to the awaiting coroutine
                outcome = (done.set_exception, e)
            else:
                outcome = (done.set_result, result)
            try:
                loop.call_soon_threadsafe(timer.cancel)
                loop.call_soon_threadsafe(give_back)
                loop.call_soon_threadsafe(settle, *outcome)
            except RuntimeError:
                pass  # the loop is closed: the service is stopping

        try:
            threading.Thread(target=work, name="module-call", daemon=True).start()
        except BaseException:
            timer.cancel()
            give_back()
            raise
        return await done


pool = ModulePool()
run = pool.run
