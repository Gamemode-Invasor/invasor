"""Small persistent JSON stores (module settings, per-game settings, user config)."""
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("invasor.storage")

# One lock per file: module methods run in worker threads, and two of them updating the
# same store at once must not lose each other's changes.
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(path):
    with _locks_guard:
        return _locks.setdefault(str(path.resolve()), threading.RLock())


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
    def __init__(self, path):
        self.path = Path(path)
        self._lock = _lock_for(self.path)

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
                os.unlink(tmp)
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
