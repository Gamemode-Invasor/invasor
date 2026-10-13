"""Background tasks with progress: what `ctx.task()` gives a module, and what the core opens
for its own long operations (updates, Market installs).

The registry is in memory only: it holds the tasks that are running and the last few that
ended, so any part of the UI can show them (`core.tasks()`), and it says when one starts or
ends (the `task` event of the module event bus). It never persists anything and never
starts a thread, so with no task open it costs nothing.
"""
import logging
import math
import threading
import time
from collections import deque
from numbers import Real

from .schema import InvalidArgument, Unavailable

log = logging.getLogger("invasor.tasks")

MAX_LABEL = 64
MAX_TEXT = 256
MAX_ACTIVE = 32  # a module that never ends its tasks can't make the registry grow without end
MAX_RECENT = 20


def _text(value, what):
    if not isinstance(value, str):
        raise InvalidArgument(f"{what} must be text")
    return value[:MAX_TEXT]


def _label(value):
    if not isinstance(value, str) or not value.strip():
        raise InvalidArgument("a task needs a label (some text)")
    return value.strip()[:MAX_LABEL]


def _fraction(value):
    """None (unknown progress) or a number from 0 to 1 (anything outside is cut to it)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise InvalidArgument("fraction must be None or a number from 0 to 1")
    return min(1.0, max(0.0, float(value)))


class Task:
    """One long operation. Its methods are quick and never block, so the same object works
    in threads and in `async def` code, and as a context manager (`with` / `async with`):
    leaving normally calls done(); leaving with an exception calls fail(str(error)) and the
    exception goes on. Once it's done or failed, further calls do nothing."""

    def __init__(self, registry, id, owner, label, now):
        self._registry = registry
        self.id = id
        self.owner = owner
        self.label = label
        self.state = "running"
        self.fraction = None
        self.text = ""
        self.started = self.updated = now
        self.finished = None

    def update(self, fraction=None, text=""):
        """New progress: `fraction` from 0 to 1 (None: unknown, no bar) and a short `text`.
        Both replace what was there before."""
        self._registry._update(self, _fraction(fraction), _text(text, "text"))

    def done(self, text=""):
        self._registry._finish(self, "done", _text(text, "text"))

    def fail(self, text):
        self._registry._finish(self, "failed", _text(text, "text"))

    def progress(self, low, high, text=""):
        """A `callback(done, total)` that maps a transfer onto the `low`..`high` stretch of the bar."""
        def report(done, total):
            share = min(1.0, done / total) if total else None
            self.update(None if share is None else low + (high - low) * share, text)
        return report

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.done()
        else:
            self.fail(str(exc) or exc_type.__name__)
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return self.__exit__(exc_type, exc, tb)

    def _view(self):
        return {
            "id": self.id, "module": self.owner, "label": self.label, "state": self.state,
            "fraction": self.fraction, "text": self.text,
            "started": self.started, "updated": self.updated, "finished": self.finished,
        }


class TaskRegistry:
    """The tasks of the whole core: `owner` is a module id, or "core". `on_event(name, payload)`
    hears `task` when one starts, ends or fails (not on every update: that would be a thread
    per step for whoever listens); it's called outside the lock and a failure in it is logged."""

    def __init__(self, on_event=None, clock=time.time, max_active=MAX_ACTIVE, max_recent=MAX_RECENT):
        self._on_event = on_event
        self._clock = clock
        self._max_active = max_active
        self._lock = threading.Lock()
        self._next = 1
        self._active = {}  # id -> Task
        self._recent = deque(maxlen=max_recent)  # Task, the newest first

    def start(self, owner, label):
        label = _label(label)
        with self._lock:
            if len(self._active) >= self._max_active:
                raise Unavailable("too many tasks running")
            task = Task(self, self._next, owner, label, self._clock())
            self._next += 1
            self._active[task.id] = task
            view = task._view()
        self._emit(view)
        return task

    def snapshot(self):
        """{"active": [...], "recent": [...]}: copies, in order (active by start, recent newest first)."""
        with self._lock:
            return {
                "active": [t._view() for t in self._active.values()],
                "recent": [t._view() for t in self._recent],
            }

    def close_owner(self, owner, text):
        """Fail what `owner` left running (its module was unloaded)."""
        with self._lock:
            left = [t for t in self._active.values() if t.owner == owner]
        for task in left:
            self._finish(task, "failed", text)

    def _update(self, task, fraction, text):
        with self._lock:
            if task.state != "running":
                return
            task.fraction, task.text, task.updated = fraction, text, self._clock()

    def _finish(self, task, state, text):
        with self._lock:
            if task.state != "running":
                return
            task.state, task.text = state, text
            if state == "done":
                task.fraction = 1.0
            task.updated = task.finished = self._clock()
            del self._active[task.id]
            self._recent.appendleft(task)
            view = task._view()
        self._emit(view)

    def _emit(self, view):
        if self._on_event is None:
            return
        try:
            self._on_event("task", view)
        except Exception:
            log.exception("task event failed")
