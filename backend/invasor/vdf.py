"""Reader for Steam's binary VDF (e.g. userdata/<id>/config/shortcuts.vdf)."""
import struct

MAP, STRING, INT32, FLOAT32, UINT64, END = 0x00, 0x01, 0x02, 0x03, 0x07, 0x08


class VDFError(ValueError):
    pass


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
    except (IndexError, ValueError, struct.error) as e:
        raise VDFError(str(e)) from e
