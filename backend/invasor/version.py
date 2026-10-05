"""Version numbers: X.Y.Z or X.Y.Z-rcN, comparable. Shared by the updater and the module contract."""
import re

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")


def parse_version(text):
    """A sortable key for X.Y.Z or X.Y.Z-rcN (optional leading v), else None: anything else
    is never offered. (X, Y, Z, 1, 0) is a stable release and (X, Y, Z, 0, N) its Nth
    release candidate, so 0.1.2-rc1 < 0.1.2-rc10 < 0.1.2 < 0.1.3-rc1."""
    m = VERSION_RE.match(text) if isinstance(text, str) else None
    if not m:
        return None
    x, y, z, rc = m.groups()
    return (int(x), int(y), int(z), 1, 0) if rc is None else (int(x), int(y), int(z), 0, int(rc))
