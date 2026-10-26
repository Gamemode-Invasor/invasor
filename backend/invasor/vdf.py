"""Readers for Steam's VDF files: binary (e.g. userdata/<id>/config/shortcuts.vdf) and text
(libraryfolders.vdf, appmanifest_<appid>.acf, loginusers.vdf, localconfig.vdf...)."""
import re
import struct

from . import schema

MAP, STRING, INT32, FLOAT32, UINT64, END = 0x00, 0x01, 0x02, 0x03, 0x07, 0x08


TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


class VDFError(schema.InvalidArgument):
    pass


def loads_text(text: str) -> dict:
    """Nested dicts of strings. A repeated key keeps its last value; a block with no name is left out."""
    if not isinstance(text, str):
        raise VDFError(f"text VDF must be a str, not {type(text).__name__}")
    root, stack = {}, []
    node, key = root, None
    for m in TOKEN.finditer(text):
        word, brace = m.groups()
        if brace == "{":
            child = {}
            if key is not None:  # a block with no name is read and left out
                node[key] = child
            stack.append(node)
            node, key = child, None
        elif brace == "}":
            if stack:
                node = stack.pop()
            key = None
        else:
            word = re.sub(r"\\(.)", lambda e: ESCAPES.get(e.group(1), e.group(1)), word)
            if key is None:
                key = word
            else:
                node[key] = word
                key = None
    return root


def get(node, *path, default=None):
    """node[path...] ignoring the case of the keys (Steam writes "Steam" and "steam")."""
    for name in path:
        if not isinstance(node, dict):
            return default
        found = next((v for k, v in node.items() if str(k).lower() == str(name).lower()), None)
        if found is None:
            return default
        node = found
    return node


def loads_binary(data: bytes) -> dict:
    pos = 0

    def cstring():
        nonlocal pos
        end = data.index(b"\0", pos)
        s = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        return s

    def unpack(fmt):
        nonlocal pos
        (v,) = struct.unpack_from(fmt, data, pos)
        pos += struct.calcsize(fmt)
        return v

    def read_map():
        nonlocal pos
        out = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == END:
                return out
            key = cstring()
            if kind == MAP:
                out[key] = read_map()
            elif kind == STRING:
                out[key] = cstring()
            elif kind == INT32:
                out[key] = unpack("<i")
            elif kind == FLOAT32:
                out[key] = unpack("<f")
            elif kind == UINT64:
                out[key] = unpack("<Q")
            else:
                raise VDFError(f"unknown VDF type 0x{kind:02x} at offset {pos - 1}")
        return out

    try:
        return read_map()
    except (IndexError, ValueError, struct.error, RecursionError) as e:
        raise VDFError(str(e)) from e
