"""Unpack zip, tar (plain, .gz, .xz, .bz2, .zst) and 7z archives safely. Every format is read
as a sequence of entries (zipfile, or tarfile; 7z and tar.zst go through bsdtar as a tar stream)
and the core writes each entry itself, so the same rules hold for all of them: nothing outside
the destination, no links unless asked, no devices, bounded in files and bytes."""
import contextlib
import lzma
import os
import posixpath
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path

from .schema import InvalidArgument, Unavailable

CHUNK = 1 << 20
# Errors of a damaged archive while reading it (bz2 raises OSError).
DAMAGE = (zipfile.BadZipFile, tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError, NotImplementedError)


class _Damaged(InvalidArgument):
    """Corrupt data: with bsdtar, its own message explains it better."""


def _guard(fn, *args):
    try:
        return fn(*args)
    except DAMAGE as e:
        raise _Damaged(f"the archive is damaged: {e}") from None


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise Unavailable("cancelled")


def _format(path):
    """'zip', 'tar' (tarfile reads it) or 'bsdtar' (7z, zstd), from the first bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(262)
    except OSError as e:
        raise InvalidArgument(f"can't read the archive: {e.strerror or e}") from None
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith((b"\x1f\x8b", b"\xfd7zXZ\x00", b"BZh")) or head[257:262] == b"ustar":
        return "tar"
    if head.startswith((b"7z\xbc\xaf\x27\x1c", b"\x28\xb5\x2f\xfd")):
        return "bsdtar"
    raise InvalidArgument("unknown archive format")


def _zip_entries(path):
    """(name, kind, mode, open) for each entry; kind is dir, file, link or other."""
    with _guard(zipfile.ZipFile, path) as zf:
        for info in zf.infolist():
            if info.flag_bits & 1:
                raise InvalidArgument(f"encrypted entry in the archive: {info.filename!r}")
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISDIR(mode):
                kind = "dir"
            elif stat.S_ISLNK(mode):
                kind = "link"
            elif stat.S_ISREG(mode) or not stat.S_IFMT(mode):
                kind = "file"
            else:
                kind = "other"
            yield info.filename, kind, mode, lambda info=info: zf.open(info)


def _tar_entries(tar):
    for m in _guard(iter, tar):
        kind = "dir" if m.isdir() else "file" if m.isreg() else "link" if m.issym() else "other"
        yield m.name, kind, m.mode, lambda m=m: tar.extractfile(m) if m.isreg() else _Link(m.linkname)


class _Link:
    """A tar link's target, read like a zip link's contents."""

    def __init__(self, target):
        self.target = target

    def read(self, n=-1):
        return os.fsencode(self.target)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@contextlib.contextmanager
def _entries(path, fmt, cancel):
    if fmt == "zip":
        with contextlib.closing(_zip_entries(path)) as gen:
            yield gen
        return
    if fmt == "tar":
        with _guard(tarfile.open, path, "r|*") as tar:
            yield _tar_entries(tar)
        return
    if not shutil.which("bsdtar"):
        raise Unavailable("bsdtar is needed to unpack 7z and tar.zst")
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(["bsdtar", "-cf", "-", "--format=pax", "@" + os.path.abspath(path)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
        try:
            with proc.stdout, _guard(tarfile.open, None, "r|", proc.stdout) as tar:
                yield _tar_entries(tar)
                while _guard(proc.stdout.read, CHUNK):  # the padding after the end, so bsdtar ends cleanly
                    _cancelled(cancel)
            failed = proc.wait() != 0
        except _Damaged:
            # The stream ended early because bsdtar stopped: let it finish and tell why.
            try:
                failed = proc.wait(timeout=5) != 0
            except subprocess.TimeoutExpired:
                failed = False
            if not failed:
                raise
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        if failed:
            err.seek(0)
            lines = err.read(4096).decode(errors="replace").strip().splitlines()
            raise InvalidArgument(f"the archive is damaged: {lines[0] if lines else 'bsdtar failed'}")


def _clean(name):
    """The entry's name as a relative posix path ('' for the archive's own root './'),
    or InvalidArgument if it could point anywhere else."""
    if not name or "\0" in name or "\\" in name or name.startswith("/") or (name[1:2] == ":" and name[0].isalpha()):
        raise InvalidArgument(f"unsafe path in the archive: {name!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts or (not parts and name.strip("./")):
        raise InvalidArgument(f"unsafe path in the archive: {name!r}")
    return "/".join(parts)


def _inside(path, root):
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


def extract(archive, dest, *, only_top=None, single_root=False, max_bytes=2 << 30,
            max_files=100_000, symlinks="refuse", cancel=None):
    """Unpack `archive` (format told by its contents) into `dest`, which must be missing or an
    empty folder. Only folders, plain files and, with symlinks="inside", relative links that stay
    inside; only the top folders in `only_top` if given; at most max_files entries and max_bytes.
    Everything goes to a temporary folder next to dest first: on any error dest is left as it was.
    `cancel` (threading.Event) stops it with Unavailable("cancelled").
    Returns the sorted relative paths written, or with single_root=True the Path of the single
    top folder the archive must hold."""
    if symlinks not in ("refuse", "inside"):
        raise InvalidArgument(f"symlinks must be 'refuse' or 'inside', not {symlinks!r}")
    dest = Path(dest)
    if not dest.parent.is_dir():
        raise InvalidArgument(f"{dest.parent} doesn't exist")
    if os.path.lexists(dest) and (dest.is_symlink() or not dest.is_dir() or any(dest.iterdir())):
        raise InvalidArgument(f"{dest} isn't an empty folder")
    fmt = _format(archive)
    tmp = tempfile.mkdtemp(dir=dest.parent, prefix=".extract-")
    try:
        with _entries(archive, fmt, cancel) as entries:
            written = _write_all(entries, tmp, only_top, max_bytes, max_files, symlinks, cancel)
        if single_root:
            top = os.listdir(tmp)
            if len(top) != 1 or not stat.S_ISDIR(os.lstat(os.path.join(tmp, top[0])).st_mode):
                raise InvalidArgument("the archive must hold a single top folder")
        os.chmod(tmp, 0o755)
        if os.path.lexists(dest):
            dest.rmdir()
        os.rename(tmp, dest)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise Unavailable(f"couldn't unpack into {dest}: {e.strerror or e}") from None
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest / top[0] if single_root else sorted(written)


def _write_all(entries, tmp, only_top, max_bytes, max_files, symlinks, cancel):
    root = os.path.realpath(tmp)
    written, dirs = set(), {""}  # explicit entries; every folder there is
    total = 0
    it = iter(entries)
    while True:
        _cancelled(cancel)
        try:
            raw, kind, mode, opener = next(it)
        except StopIteration:
            return written
        except DAMAGE as e:
            raise _Damaged(f"the archive is damaged: {e}") from None
        name = _clean(raw)
        if not name:
            if kind == "dir":
                continue  # './': the archive's own root
            raise InvalidArgument(f"unsafe path in the archive: {raw!r}")
        if only_top is not None and name.split("/")[0] not in only_top:
            continue
        if name in written or (name in dirs and kind != "dir"):
            raise InvalidArgument(f"the archive holds {name!r} twice")
        if kind == "other" or (kind == "link" and symlinks == "refuse"):
            raise InvalidArgument(f"unexpected entry in the archive: {name!r}")
        written.add(name)
        if len(written) > max_files:
            raise InvalidArgument(f"the archive holds more than {max_files} files")
        parent, _, _ = name.rpartition("/")
        _make_dirs(tmp, parent, dirs, written, name)
        path = os.path.join(tmp, name)
        if kind == "dir":
            if name not in dirs:
                os.mkdir(path)
                os.chmod(path, 0o755)
                dirs.add(name)
            continue
        if not _inside(os.path.dirname(path), root):
            raise InvalidArgument(f"unsafe path in the archive: {name!r}")
        with _guard(opener) as src:
            if kind == "link":
                target = os.fsdecode(_guard(src.read, 4096))
                where = posixpath.normpath(posixpath.join(parent, target))
                if not target or "\0" in target or target.startswith("/") or where == ".." or where.startswith("../"):
                    raise InvalidArgument(f"link pointing outside the archive: {name!r} -> {target!r}")
                os.symlink(target, path)
                continue
            total = _copy(src, path, 0o755 if mode & 0o111 else 0o644, total, max_bytes, cancel, name)


def _make_dirs(tmp, parent, dirs, written, name):
    """Create the missing folders above an entry (7z may list a file before its folder)."""
    parts = parent.split("/") if parent else []
    for i in range(len(parts)):
        sub = "/".join(parts[:i + 1])
        if sub in dirs:
            continue
        if sub in written:
            raise InvalidArgument(f"{name!r} is inside {sub!r}, which isn't a folder")
        os.mkdir(os.path.join(tmp, sub))
        os.chmod(os.path.join(tmp, sub), 0o755)
        dirs.add(sub)


def _copy(src, path, mode, total, max_bytes, cancel, name):
    """Write one file, counting the bytes really written. Returns the new total."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise InvalidArgument(f"the archive holds {name!r} twice") from None
    with os.fdopen(fd, "wb") as out:
        os.fchmod(fd, mode)
        while True:
            _cancelled(cancel)
            chunk = _guard(src.read, CHUNK)
            if not chunk:
                return total
            total += len(chunk)
            if total > max_bytes:
                raise InvalidArgument(f"the archive holds more than {max_bytes} bytes")
            out.write(chunk)


def swap_dir(new, target):
    """Put folder `new` where `target` is (target may not exist), replacing it in one rename:
    the old one is moved aside first and removed after. If `new` is on another filesystem it
    is copied next to target first (and the original removed). Returns target as a Path."""
    new, target = Path(new), Path(target)
    if new.is_symlink() or not new.is_dir():
        raise InvalidArgument(f"{new} isn't a folder")
    if not target.parent.is_dir():
        raise InvalidArgument(f"{target.parent} doesn't exist")
    if os.path.lexists(target) and (target.is_symlink() or not target.is_dir()):
        raise InvalidArgument(f"{target} isn't a folder")
    tag = secrets.token_hex(4)
    copy = old = None
    try:
        src = new
        if os.stat(new).st_dev != os.stat(target.parent).st_dev:
            copy = target.parent / f".{target.name}.new-{tag}"
            shutil.copytree(new, copy, symlinks=True)
            src = copy
        if os.path.lexists(target):
            old = target.parent / f".{target.name}.old-{tag}"
            os.rename(target, old)
        try:
            os.rename(src, target)
        except OSError:
            if old:
                os.rename(old, target)
            raise
    except OSError as e:
        if copy:
            shutil.rmtree(copy, ignore_errors=True)
        raise Unavailable(f"couldn't put {target.name} in place: {e.strerror or e}") from None
    if old:
        shutil.rmtree(old, ignore_errors=True)
    if copy:
        shutil.rmtree(new, ignore_errors=True)
    return target

