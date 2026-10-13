"""Tells every Steam window the state of the tasks when it changes, so the panel and the
handle's dot don't have to ask: nothing runs while no task changes.

The state is pushed to the pages the same way the combo opens the panel (a call evaluated in
each window). It is always the whole snapshot, never a single change: a window that missed one
(just reloaded, Steam restarted) is up to date again with the next, and a page that loads asks
once for itself anyway (core.tasks). Changes come from any thread (a module downloading, the
updater); a burst of them, like a download reporting every chunk, becomes one push, and
pushes are at least `min_gap` seconds apart."""
import asyncio
import json
import logging
import threading

log = logging.getLogger("invasor.taskpush")


class TaskPusher:
    def __init__(self, snapshot, send, loop, min_gap=1.0, sleep=asyncio.sleep):
        """`snapshot()` -> the tasks as core.tasks() gives them; `await send(js)` runs `js` in the
        windows; `loop` is the service's event loop."""
        self._snapshot = snapshot
        self._send = send
        self._loop = loop
        self._min_gap = min_gap
        self._sleep = sleep
        self._lock = threading.Lock()
        self._dirty = False  # something changed since the last push started
        self._running = False  # a push loop is scheduled or running
        self._closed = False
        self._task = None

    def notify(self):
        """Something changed. Quick and safe from any thread: it only marks it, and wakes the loop if it was idle."""
        with self._lock:
            self._dirty = True
            if self._running or self._closed:
                return
            self._running = True
        try:
            self._loop.call_soon_threadsafe(self._start)
        except RuntimeError:  # the loop is gone: the service is stopping
            with self._lock:
                self._running = False

    def stop(self):
        """No more pushes (call it on the loop's thread)."""
        with self._lock:
            self._closed = True
        if self._task is not None:
            self._task.cancel()

    def _start(self):
        self._task = asyncio.ensure_future(self._run())

    async def _run(self):
        try:
            while True:
                with self._lock:
                    if not self._dirty or self._closed:
                        self._running = False
                        return
                    self._dirty = False
                try:
                    await self._send(f"window.__invasor?.tasks?.({json.dumps(self._snapshot())})")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("couldn't push the tasks to the windows")
                await self._sleep(self._min_gap)
        except asyncio.CancelledError:
            with self._lock:
                self._running = False
            raise
