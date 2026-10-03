#!/usr/bin/env python3
"""Install a module into ~/.local/share/invasor/user-modules (outside the core, so
updating Invasor never removes it), with the same checks as ⚙ Settings:

    python3 tools/install_module.py <module folder or .zip>
    python3 tools/install_module.py --uninstall <id> [--purge]

A folder is packed first (tools/pack_module.py: build, check, tests). The service is
restarted afterwards so every window picks the module up. Uninstalling stops the
service first and runs the module's uninstall() (without Steam); --purge also deletes
its settings and data.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True

from invasor import install, schema  # noqa: E402
from invasor.context import GameContext  # noqa: E402
from invasor.modules import ModuleManager  # noqa: E402
from invasor.schema import InvalidArgument  # noqa: E402

RESERVED = {"core"} | {d.name for d in (ROOT / "modules").iterdir() if d.is_dir()}


def service(action):
    subprocess.run(["systemctl", "--user", action, "invasor"], check=False)


def uninstall(mid, purge):
    """As ⚙ Settings does: the module's uninstall() first, then its files."""
    if not schema.ID_RE.match(mid) or not (install._user_dir() / mid).is_dir():
        raise InvalidArgument(f"{mid} isn't an installed module")
    manager = ModuleManager({"disabled_modules": []}, GameContext())
    manager._read(install._user_dir() / mid, "user")
    manager.remove(mid, purge)
    install.uninstall(mid)


def main(argv):
    if not argv:
        sys.exit(__doc__)
    try:
        if argv[0] == "--uninstall" and len(argv) in (2, 3):
            purge = argv[2:] == ["--purge"]
            if len(argv) == 3 and not purge:
                sys.exit(__doc__)
            service("stop")
            try:
                uninstall(argv[1], purge)
            finally:
                service("start")
            print(f"uninstalled {argv[1]}" + (" and its settings and data" if purge else " (its settings in ~/.config/invasor are kept)"))
            return
        else:
            src = Path(argv[0]).resolve()
            with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
                if src.is_dir():
                    import pack_module
                    src = pack_module.main([str(src), "--out", tmp])
                # install.* takes paths relative to the home folder (as the panel does).
                rel = src.relative_to(Path.home())
                mid = install.install(str(rel), RESERVED, replace=True)
            print(f"installed {mid} in {install._user_dir() / mid}")
    except InvalidArgument as e:
        sys.exit(f"not installed: {e}")
    service("restart")


if __name__ == "__main__":
    main(sys.argv[1:])
