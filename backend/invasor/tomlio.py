"""TOML for modules (ctx.toml): read other programs' config files and write them back
without losing anything.

Reading is Python's own tomllib (3.11+). Writing covers the subset such config files
use: tables, arrays of tables, strings, integers, floats, booleans and arrays of those.
Every write is checked by reading the result back before it replaces the file, so a
config is never left in a state its program can't read. Order and keys we don't know
about are kept.
"""
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

from .schema import InvalidArgument, Unavailable
from .storage import fsync_dir

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
BACKUP_SUFFIX = ".invasor-backup"


def _need_tomllib():
    if tomllib is None:
        raise Unavailable("reading TOML needs Python 3.11 or newer")


def load(path):
    """(data, mtime_ns) of a TOML file; ({}, None) if it doesn't exist.
    InvalidArgument if it isn't valid TOML (nothing is ever written over it then)."""
    _need_tomllib()
    path = Path(path)
    try:
        raw = path.read_bytes()
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return {}, None
    try:
        return tomllib.loads(raw.decode("utf-8")), mtime
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise InvalidArgument(f"{path.name} isn't valid TOML ({e}): fix or remove it first") from None


def _key(k):
    if not isinstance(k, str) or not k:
        raise ValueError(f"invalid TOML key {k!r}")
    return k if BARE_KEY.match(k) else _string(k)


def _string(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("TOML here can't hold inf/nan")
        text = repr(v)
        return text if any(c in text for c in ".en") else text + ".0"
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        if any(isinstance(x, (dict, list)) for x in v):
            raise ValueError("nested arrays/tables inside arrays aren't supported here")
        return "[ " + ", ".join(_value(x) for x in v) + " ]" if v else "[]"
    raise ValueError(f"can't write {type(v).__name__} to TOML")


def _is_table_array(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _table(lines, prefix, data):
    """Plain keys first (TOML requires it), then sub-tables, then arrays of tables."""
    for k, v in data.items():
        if not isinstance(v, dict) and not _is_table_array(v):
            lines.append(f"{_key(k)} = {_value(v)}")
    for k, v in data.items():
        if isinstance(v, dict):
            name = prefix + [_key(k)]
            lines += ["", f"[{'.'.join(name)}]"]
            _table(lines, name, v)
    for k, v in data.items():
        if _is_table_array(v):
            name = prefix + [_key(k)]
            for item in v:
                lines += ["", f"[[{'.'.join(name)}]]"]
                _table(lines, name, item)


def dumps(data):
    lines = []
    _table(lines, [], data)
    return "\n".join(lines).lstrip("\n") + "\n"


def save(path, data, expected_mtime=None, backup=True, validate=None):
    """Write `data` as TOML. Refused (Unavailable) if the file changed since it was read
    with mtime `expected_mtime` (another program saved meanwhile: read it again).
    The first time a file is changed, the original is kept next to it (*.invasor-backup).
    validate(temp_path), if given, checks the new file before it replaces the original
    (e.g. the program's own validator); it raises to refuse it, and nothing changes."""
    _need_tomllib()
    path = Path(path)
    try:
        current = path.stat().st_mtime_ns
    except FileNotFoundError:
        current = None
    if expected_mtime is not None and current != expected_mtime:
        raise Unavailable(f"{path.name} was changed by another program: reload and try again")
    try:
        text = dumps(data)
    except ValueError as e:
        raise InvalidArgument(str(e)) from None
    # Never replace a config with something its program couldn't read back the same.
    if tomllib.loads(text) != data:
        raise Unavailable(f"refusing to write {path.name}: the result wouldn't read back the same")
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = path.with_name(path.name + BACKUP_SUFFIX)
    if backup and current is not None and not keep.exists():
        shutil.copy2(path, keep)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if current is not None:
            shutil.copymode(path, tmp)
        if validate is not None:
            validate(Path(tmp))
        os.replace(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        os.unlink(tmp)
        raise
    return path.stat().st_mtime_ns
