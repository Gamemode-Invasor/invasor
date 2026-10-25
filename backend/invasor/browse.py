"""One folder for ctx.pickPath's browser (core.browse_path), ported from Companion's and Opti's."""
import os
from pathlib import Path

from .schema import InvalidArgument

LIMIT = 1000
# Folders whose numbered subfolders are appids: shown as "appid - game".
APPID_FOLDERS = {"compatdata", "shadercache"}


def _folder(path):
    """The real folder to list: home when not given, or when it doesn't exist (a stale `start`)."""
    if path is None or path == "":
        return Path(os.path.realpath(Path.home()))
    if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 4096 or "\0" in path:
        raise InvalidArgument("path must be an absolute path")
    real = os.path.realpath(path)
    return Path(real if os.path.isdir(real) else os.path.realpath(Path.home()))


def _exts(ext):
    if ext is None:
        return None
    if (not isinstance(ext, list) or len(ext) > 20
            or not all(isinstance(e, str) and e.startswith(".") and 2 <= len(e) <= 16 for e in ext)):
        raise InvalidArgument("ext must be a list of up to 20 endings like \".dll\"")
    return tuple(e.lower() for e in ext)


def listdir(path=None, *, files=False, ext=None, hidden=False, name_of=lambda appid: None, limit=LIMIT):
    """{"path", "parent", "dirs": [{"name", "label"}], "files": [{"name"}], "error"}.

    Hidden entries left out unless `hidden`, folders before files (only listed with `files`, filtered by `ext`), each sorted
    case-insensitively, at most `limit` entries in all. Links are followed; broken ones and special files are
    skipped. A folder that can't be read comes back with "error" and nothing listed.
    """
    exts = _exts(ext)
    folder = _folder(path)
    parent = None if folder == folder.parent else str(folder.parent)
    out = {"path": str(folder), "parent": parent, "dirs": [], "files": [], "error": None}
    dirs, names = [], []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if not hidden and e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        dirs.append(e.name)
                    elif files and e.is_file() and (exts is None or e.name.lower().endswith(exts)):
                        names.append(e.name)
                except OSError:
                    continue
    except OSError as e:
        out["error"] = e.strerror or str(e)
        return out
    appids = folder.name in APPID_FOLDERS
    for name in sorted(dirs, key=str.lower)[:limit]:
        game = name_of(name) if appids and name.isascii() and name.isdigit() else None
        out["dirs"].append({"name": name, "label": f"{name} - {game}" if game else name})
    out["files"] = [{"name": n} for n in sorted(names, key=str.lower)[: max(0, limit - len(out["dirs"]))]]
    return out
