"""Small persistent JSON stores (module settings, per-game settings, user config)."""
import json
import logging
import os
import tempfile
import threading
import weakref
from pathlib import Path

from .schema import Unavailable

log = logging.getLogger("invasor.storage")

# One lock per file: module methods run in worker threads, and two of them updating the
# same store at once must not lose each other's changes.
# Weak: a lock lives while a store uses it, so one per appid doesn't pile up forever.
_locks = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def _lock_for(path):
    key = str(path.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def fsync_dir(path):
    """Make a rename inside `path` durable. Best effort: not every filesystem allows it."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class JsonStore:
    def __init__(self, path, alive=None):
        """alive(): False once whoever owns the store is gone (an unloaded module): it
        then refuses to write, so a thread that outlives its module can't bring back
        files that were just purged."""
        self.path = Path(path)
        self._lock = _lock_for(self.path)
        self._alive = alive

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            # Moved aside (kept for diagnosis), so it's reported once and the next save
            # starts clean instead of tripping over it on every read.
            aside = self.path.with_name(self.path.name + ".corrupt")
            log.error("corrupt %s (%s): moved to %s, using empty data", self.path, e, aside.name)
            try:
                os.replace(self.path, aside)
            except OSError:
                pass
            return {}

    def save(self, data):
        # Write-then-rename so a crash or power loss never leaves half a file.
        with self._lock:
            if self._alive is not None and not self._alive():
                raise Unavailable("this module was unloaded: it can't save anymore")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())  # the data must be on disk before the rename makes it the file
                os.replace(tmp, self.path)
                fsync_dir(self.path.parent)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass  # already renamed: don't hide the real error
                raise

    def transform(self, fn):
        """Atomically replace the data with fn(data) (saved only if it changed)."""
        with self._lock:
            data = self.load()
            new = fn(dict(data))
            if new != data:
                self.save(new)
            return new

    def update(self, **changes):
        with self._lock:
            data = self.load()
            data.update(changes)
            self.save(data)
            return data
