"""Background tasks with progress: what `ctx.task()` gives a module, and what the core opens
for its own long operations (updates, Market installs).

The registry is in memory only: it holds the tasks that are running and the last few that
ended, so any part of the UI can show them (`core.tasks()`), and it says when one starts or
ends (the `task` event of the module event bus). A task may also stop to ask the user a
question (Task.ask, answered with TaskRegistry.answer). It never persists anything and never
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
ASK_TIMEOUT = 600  # seconds a question waits for the user before it counts as a no


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
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidArgument("fraction must be None or a number from 0 to 1")
    try:
        number = float(value)
    except OverflowError:  # an int too big for a float: past the edge it is cut to
        return 1.0 if value > 0 else 0.0
    if not math.isfinite(number):
        raise InvalidArgument("fraction must be None or a number from 0 to 1")
    return min(1.0, max(0.0, number))


class Task:
    """One long operation. Its methods (but ask()) are quick and never block, so the same object works
    in threads and in `async def` code, and as a context manager (`with` / `async with`):
    leaving normally calls done(); leaving with an exception calls fail(str(error)) and the
    exception goes on. Once it's done or failed, further calls do nothing."""

    def __init__(self, registry, id, owner, label, now, on_event=None):
        self._registry = registry
        self._on_event = on_event
        self.id = id
        self.owner = owner
        self.label = label
        self.state = "running"
        self.fraction = None
        self.text = ""
        self.question = None  # while waiting: what the user is asked
        self._pending = None  # while waiting: {"woken": threading.Event, "accept": bool}
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

    def ask(self, question, timeout=ASK_TIMEOUT):
        """Ask the user `question` (Accept / Deny in the Tasks panel) and wait for the answer:
        True for Accept; False for Deny, after `timeout` seconds, if the task ends meanwhile (its
        module unloaded, say) or isn't running. While it waits, update() is ignored.
        It blocks: never call it from the event loop's thread (a plain, threaded method is fine)."""
        if not isinstance(question, str) or not question.strip():
            raise InvalidArgument("a question needs some text")
        return self._registry._ask(self, question.strip()[:MAX_TEXT], timeout)

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
            "fraction": self.fraction, "text": self.text, "question": self.question,
            "started": self.started, "updated": self.updated, "finished": self.finished,
        }


class TaskRegistry:
    """The tasks of the whole core: `owner` is a module id, or "core". `on_event(name, payload)`
    hears `task` when one starts, waits for an answer, ends or fails (not on every update: that would be a thread
    per step for whoever listens). `watch(fn)` is for whoever must know of every change, updates
    included, and only that something changed (fn() takes nothing and must be quick: it's called
    from the thread that changed it). Both are called outside the lock and a failure is logged."""

    def __init__(self, on_event=None, clock=time.time, max_active=MAX_ACTIVE, max_recent=MAX_RECENT):
        self._on_event = on_event
        self._watchers = []
        self._clock = clock
        self._max_active = max_active
        self._lock = threading.Lock()
        self._next = 1
        self._active = {}  # id -> Task
        self._recent = deque(maxlen=max_recent)  # Task, the newest first

    def start(self, owner, label, on_event=None):
        """Open a task. `on_event(phase, view)`, if given, hears just this task: "start" now and
        "end" when it is done or failed (not when `close_owner` closes it). Called outside the
        lock; a failure is logged."""
        label = _label(label)
        with self._lock:
            if len(self._active) >= self._max_active:
                raise Unavailable("too many tasks running")
            task = Task(self, self._next, owner, label, self._clock(), on_event)
            self._next += 1
            self._active[task.id] = task
            view = task._view()
        self._emit(view)
        self._task_event(task, "start", view)
        self._changed()
        return task

    def watch(self, fn):
        """fn() whenever any task starts, advances, ends or fails."""
        self._watchers.append(fn)

    def snapshot(self):
        """{"active": [...], "recent": [...]}: copies, in order (active by start, recent newest first)."""
        with self._lock:
            return {
                "active": [t._view() for t in self._active.values()],
                "recent": [t._view() for t in self._recent],
            }

    def clear_recent(self):
        """Forget the tasks that ended (never the ones still running). How many were forgotten."""
        with self._lock:
            count = len(self._recent)
            self._recent.clear()
        if count:
            self._changed()
        return count

    def answer(self, task_id, accept):
        """The user's answer to a waiting task's question: True if one was waiting with that id."""
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            raise InvalidArgument("the task id must be a whole number")
        if not isinstance(accept, bool):
            raise InvalidArgument("accept must be True or False")
        with self._lock:
            task = self._active.get(task_id)
            if task is None or task.state != "waiting":
                return False
            task._pending["accept"] = accept
            self._resume(task)
        self._changed()
        return True

    def close_owner(self, owner, text):
        """Fail what `owner` left running (its module was unloaded)."""
        with self._lock:
            left = [t for t in self._active.values() if t.owner == owner]
        for task in left:
            self._finish(task, "failed", text, quiet=True)

    def _update(self, task, fraction, text):
        with self._lock:
            if task.state != "running":
                return
            task.fraction, task.text, task.updated = fraction, text, self._clock()
        self._changed()

    def _ask(self, task, question, timeout):
        with self._lock:
            if task.state != "running":
                return False
            task.state, task.question, task.updated = "waiting", question, self._clock()
            mine = task._pending = {"woken": threading.Event(), "accept": False}
            view = task._view()
        self._emit(view)
        self._changed()
        mine["woken"].wait(timeout)
        with self._lock:
            timed_out = task._pending is mine  # nobody answered: a no
            if timed_out:
                self._resume(task)
        if timed_out:
            self._changed()
        return mine["accept"]

    def _resume(self, task):
        """Back to running after a question (under the lock)."""
        task.state, task.question, task.updated = "running", None, self._clock()
        task._pending["woken"].set()
        task._pending = None

    def _finish(self, task, state, text, quiet=False):
        with self._lock:
            if task.state not in ("running", "waiting"):
                return
            if task._pending is not None:  # the one asking wakes up to a no
                task._pending["woken"].set()
                task._pending = None
            task.state, task.text, task.question = state, text, None
            if state == "done":
                task.fraction = 1.0
            task.updated = task.finished = self._clock()
            del self._active[task.id]
            self._recent.appendleft(task)
            view = task._view()
        self._emit(view)
        if not quiet:
            self._task_event(task, "end", view)
        self._changed()

    def _task_event(self, task, phase, view):
        if task._on_event is None:
            return
        try:
            task._on_event(phase, view)
        except Exception:
            log.exception("task event callback failed")

    def _changed(self):
        for fn in list(self._watchers):
            try:
                fn()
            except Exception:
                log.exception("task watcher failed")

    def _emit(self, view):
        if self._on_event is None:
            return
        try:
            self._on_event("task", view)
        except Exception:
            log.exception("task event failed")
