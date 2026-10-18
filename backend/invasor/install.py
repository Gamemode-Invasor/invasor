"""Installing user modules from a zip (⚙ Settings › Install module).

Everything is checked by the core before a module can run:
- a real zip, within size and file-count limits, with no absolute paths, no "..",
  no symlinks;
- exactly one module: its files in a single top-level folder named after its id
  (like modules/<id>/; tools/pack_module.py makes such zips);
- module.json valid for this Invasor (schema.parse_manifest, module API, min_core);
- an id that isn't reserved ("core" or a module shipped with Invasor);
- a built UI (dist/ui.js) if it has ui.ts — nothing is compiled on the console;
- backend.py imports cleanly, in a separate process (invasor.checkmod), setup() not run.
  Only when installing, after the user confirmed: importing it runs the module's code. The
  separate process catches a broken module; it isn't a sandbox.
inspect() (what the panel shows before asking) does every check but that one: choosing a zip
runs nothing of it. It hands back the zip's sha256, and install() given that sha256 refuses a
zip that changed meanwhile: what is installed is the zip the user confirmed. Both work on a
copy of the zip made while hashing it, never on the file the user picked.
Then it's put in place atomically in USER_MODULES_DIR/<id>; a previous version is
replaced only when the caller says so.
"""
import hashlib
import hmac
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__, config, schema
from .schema import InvalidArgument, Unavailable
from .updater import SHA_RE

MAX_ZIP_BYTES = 64 << 20
MAX_UNPACKED_BYTES = 128 << 20
MAX_FILES = 2000
CHECK_TIMEOUT = 30
HOME = Path.home()


def _user_dir():
    return config.USER_MODULES_DIR


# ---------- browsing for a zip (the panel's own file picker) ----------

def downloads_dir():
    """The user's downloads folder: XDG_DOWNLOAD_DIR from ~/.config/user-dirs.dirs
    (localised, e.g. ~/Descargas), else ~/Downloads, else None."""
    try:
        for line in (HOME / ".config/user-dirs.dirs").read_text().splitlines():
            if line.startswith("XDG_DOWNLOAD_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(HOME))
                p = Path(value)
                if p.is_dir():
                    return p
    except OSError:
        pass
    return HOME / "Downloads" if (HOME / "Downloads").is_dir() else None


def browse(path=None):
    """{path, parent, entries: [{name, kind: "dir"|"zip", size}]} of a folder under the
    home directory (default: the downloads folder, else ~). Hidden entries are skipped."""
    base = HOME.resolve()
    if path is None:  # first open: the downloads folder ("" is the home itself)
        target = downloads_dir() or HOME
        if base not in target.resolve().parents:  # XDG_DOWNLOAD_DIR on another disk: not browsable
            target = HOME
    else:
        target = (HOME / str(path)).resolve()
    target = target.resolve()
    if target != base and base not in target.parents:
        raise InvalidArgument("only folders inside your home can be browsed")
    if not target.is_dir():
        raise InvalidArgument(f"{path} isn't a folder")
    entries = []
    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        raise Unavailable(f"can't read {target}: {e.strerror}") from None
    for p in items:
        if p.name.startswith("."):
            continue
        try:
            if p.is_dir():
                entries.append({"name": p.name, "kind": "dir", "size": None})
            elif p.suffix.lower() == ".zip" and p.is_file():
                entries.append({"name": p.name, "kind": "zip", "size": p.stat().st_size})
        except OSError:
            continue
    rel = target.relative_to(base)
    return {
        "path": "" if str(rel) == "." else str(rel),
        "parent": None if target == base else ("" if target.parent == base else str(target.parent.relative_to(base))),
        "entries": entries,
    }


def _zip_path(path):
    p = (HOME / str(path)).resolve()
    if HOME.resolve() not in p.parents or p.suffix.lower() != ".zip" or not p.is_file():
        raise InvalidArgument("choose a .zip file inside your home folder")
    if p.stat().st_size > MAX_ZIP_BYTES:
        raise InvalidArgument(f"the zip is bigger than {MAX_ZIP_BYTES >> 20} MB")
    return p


# ---------- checking a zip ----------

def _members(zf):
    """The zip's file entries, checked; and the prefix (single top folder) to strip."""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        raise InvalidArgument("the zip is empty")
    if len(infos) > MAX_FILES:
        raise InvalidArgument(f"the zip has more than {MAX_FILES} files")
    total = 0
    for i in infos:
        name = i.filename
        parts = PurePosixPath(name).parts
        if not parts:  # "" or "." : not a file
            raise InvalidArgument(f"unsafe path in the zip: {name!r}")
        if name.startswith("/") or "//" in name or "\\" in name or ".." in parts or (parts and ":" in parts[0]):
            raise InvalidArgument(f"unsafe path in the zip: {name!r}")
        if stat.S_ISLNK(i.external_attr >> 16):
            raise InvalidArgument(f"the zip contains a symbolic link: {name!r}")
        total += i.file_size
    if total > MAX_UNPACKED_BYTES:
        raise InvalidArgument(f"the zip unpacks to more than {MAX_UNPACKED_BYTES >> 20} MB")
    names = [i.filename for i in infos]
    tops = {PurePosixPath(n).parts[0] for n in names}
    if len(tops) == 1 and f"{next(iter(tops))}/module.json" in names:
        return infos, next(iter(tops)) + "/"
    if "module.json" in names:
        raise InvalidArgument("put the module's files inside a folder named after its id (e.g. artwork/module.json)")
    raise InvalidArgument("not an Invasor module: no <id>/module.json in the zip")


def _check_backend(d):
    """Import backend.py in a separate process (never inside the service)."""
    if not (d / "backend.py").exists():
        return
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        run = subprocess.run(
            [sys.executable, "-m", "invasor.checkmod", str(d)],
            cwd=Path(__file__).resolve().parents[1], env=env,
            capture_output=True, text=True, timeout=CHECK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise InvalidArgument(f"backend.py took more than {CHECK_TIMEOUT}s to import") from None
    try:
        result = json.loads(run.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise InvalidArgument(f"backend.py couldn't be checked: {run.stderr.strip()[-300:] or 'no output'}") from None
    if not result.get("ok"):
        raise InvalidArgument(result.get("error") or "backend.py doesn't import")
    if run.returncode != 0:
        # The verdict line is the module's word too (atexit, a replaced sys.stdout): the exit status is ours.
        raise InvalidArgument(f"backend.py couldn't be checked: {run.stderr.strip()[-300:] or 'exited with an error'}")


def _copy_hashed(src, dst):
    """Copy src to dst; the sha256 (hex) of what was copied. The size limit holds here too: the
    file may have grown since _zip_path looked at it."""
    h = hashlib.sha256()
    size = 0
    with open(src, "rb") as f, open(dst, "wb") as out:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            size += len(chunk)
            if size > MAX_ZIP_BYTES:
                raise InvalidArgument(f"the zip is bigger than {MAX_ZIP_BYTES >> 20} MB")
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def _unpack(zip_path, reserved, sha256=None):
    """Copy the zip into a fresh staging folder inside the user modules dir, unpack the copy
    there and check it all, without running any of its code. With `sha256`, a zip that isn't
    that one is refused. Returns (staging dir, module dir in it, manifest, the zip's sha256).
    The caller removes staging."""
    base = _user_dir()
    base.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=base))
    try:
        copy = staging / "module.zip"
        digest = _copy_hashed(zip_path, copy)
        if sha256 is not None and not hmac.compare_digest(digest, sha256.lower()):
            raise InvalidArgument("the zip changed since it was checked: choose it again")
        try:
            zf = zipfile.ZipFile(copy)
        except (zipfile.BadZipFile, OSError) as e:
            raise InvalidArgument(f"not a valid zip: {e}") from None
        with zf:
            infos, prefix = _members(zf)
            root = staging / "module"
            real_root = os.path.realpath(root)
            for i in infos:
                rel = i.filename[len(prefix):] if prefix else i.filename
                if not rel or rel.endswith("/"):
                    continue
                target = root / rel
                # The last word: whatever the names looked like, nothing is written outside root.
                if os.path.commonpath([real_root, os.path.realpath(target)]) != real_root:
                    raise InvalidArgument(f"unsafe path in the zip: {i.filename!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        try:
            raw = json.loads((root / "module.json").read_text())
        except ValueError as e:
            raise InvalidArgument(f"module.json isn't valid JSON: {e}") from None
        mid = prefix.rstrip("/")  # the folder name is the id, as in modules/<id>/
        try:
            manifest = schema.parse_manifest(raw, mid)
        except (schema.SchemaError, ArithmeticError) as e:
            raise InvalidArgument(f"module.json: {e}") from None
        problem = schema.core_problem(manifest, __version__)
        if problem:
            raise InvalidArgument(f"{manifest['name']} {problem}")
        if mid in reserved:
            raise InvalidArgument(f"{mid!r} is reserved (it's part of Invasor)")
        if (root / "ui.ts").exists() and not (root / "dist" / "ui.js").is_file():
            raise InvalidArgument("the module has ui.ts but no built dist/ui.js (build it before packing)")
        return staging, root, manifest, digest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def inspect(path, reserved):
    """What the zip holds, checked without installing it and without running any of its code:
    {id, name, version, description, author (name only), installed_version, sha256}. Its
    backend.py is checked by install(), which takes this sha256 to install this very zip."""
    staging, _, manifest, digest = _unpack(_zip_path(path), reserved)
    try:
        current = None
        existing = _user_dir() / manifest["id"] / "module.json"
        if existing.is_file():
            try:
                current = json.loads(existing.read_text()).get("version")
            except ValueError:
                current = "?"
        return {"id": manifest["id"], "name": manifest["name"], "version": manifest["version"],
                "description": manifest["description"], "author": manifest["author_name"],
                "installed_version": current, "sha256": digest}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


_install_lock = threading.Lock()


def install(path, reserved, replace=False, before_replace=None, sha256=None):
    """Check and put the module in USER_MODULES_DIR/<id>. An installed one is replaced
    only with replace=True (before_replace(id) is called first, e.g. to tear it down).
    `sha256` (from inspect()): install only that zip. Its backend.py is imported here, in a
    separate process: the user has said yes. One install at a time: two for the same id
    would swap the folder under each other. Returns the module id."""
    if sha256 is not None and not (isinstance(sha256, str) and SHA_RE.match(sha256)):
        raise InvalidArgument("sha256 must be the 64 hex digits inspect gave")
    with _install_lock:
        return _install(path, reserved, replace, before_replace, sha256)


def _install(path, reserved, replace, before_replace, sha256):
    staging, root, manifest, _ = _unpack(_zip_path(path), reserved, sha256)
    mid = manifest["id"]
    target = _user_dir() / mid
    try:
        # Before anything installed is touched: a new version that doesn't import leaves the old one running.
        _check_backend(root)
        if target.exists():
            if not replace:
                raise InvalidArgument(f"{manifest['name']} is already installed")
            if before_replace:
                before_replace(mid)
            old = staging / "old"
            os.replace(target, old)  # same filesystem: atomic
            try:
                os.replace(root, target)
            except OSError:
                os.replace(old, target)  # put the previous version back before staging goes
                raise
        else:
            os.replace(root, target)
        return mid
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def uninstall(mid):
    """Delete an installed user module's files (its settings in ~/.config stay)."""
    if not schema.ID_RE.match(str(mid)):
        raise InvalidArgument(f"invalid module id {mid!r}")
    target = _user_dir() / mid
    if not target.is_dir():
        raise InvalidArgument(f"{mid} isn't an installed module")
    trash = Path(tempfile.mkdtemp(prefix=".removing-", dir=_user_dir()))
    os.replace(target, trash / mid)
    shutil.rmtree(trash, ignore_errors=True)
