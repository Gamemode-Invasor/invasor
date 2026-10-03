#!/usr/bin/env python3
"""Check, build and pack a module into an installable zip:

    python3 tools/pack_module.py <module folder> [--out <dir>]

The folder's name is the module id (e.g. invasor-ducky/ducky). Steps:
1. typecheck and build its ui.ts (if any) with this core's kit -> <module>/dist/ui.js;
2. tools/check_module.py (module.json, backend import, the module's own tests);
3. zip it as <id>/… with only what the console needs: module.json, the Python files
   (not tests/), dist/ui.js, README*/LICENSE*. Written as <id>-<version>.zip.

The zip installs from ⚙ Settings › Install module, or with tools/install_module.py.
"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
sys.dont_write_bytecode = True


def run(cmd, **kw):
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(map(str, cmd))}")


def typecheck_and_build(d):
    if not (d / "ui.ts").exists():
        return
    # A throwaway tsconfig: this module's ui.ts against the core's real types.
    cfg = FRONTEND / ".tsconfig.module.json"
    cfg.write_text(json.dumps({
        "extends": "./tsconfig.json",
        "include": ["src", str(d / "ui.ts")],
    }))
    try:
        run(["npx", "--no-install", "tsc", "--noEmit", "-p", str(cfg)], cwd=FRONTEND)
    finally:
        cfg.unlink(missing_ok=True)
    run(["node", "build.mjs", "--module", str(d)], cwd=FRONTEND, stdout=subprocess.DEVNULL)


def files_to_pack(d):
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if not p.is_file() or rel.parts[0] in ("tests", "node_modules", "__pycache__") or "__pycache__" in rel.parts:
            continue
        if rel.name.startswith("."):
            continue
        if rel == Path("module.json") or rel == Path("dist/ui.js") or (len(rel.parts) == 1 and (
                rel.suffix == ".py" or rel.stem.upper() in ("README", "LICENSE", "COPYING"))):
            yield p, rel


def pack(d, out):
    manifest = json.loads((d / "module.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"{d.name}-{manifest.get('version', '0')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        packed = set()
        for p, rel in files_to_pack(d):
            z.write(p, f"{d.name}/{rel.as_posix()}")
            packed.add(rel.stem.upper())
        # A module repository keeps its LICENSE at the root, next to the module folder.
        if "LICENSE" not in packed and (d.parent / "LICENSE").is_file():
            z.write(d.parent / "LICENSE", f"{d.name}/LICENSE")
    return zip_path


def main(argv):
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    d = Path(argv[0]).resolve()
    out = Path(argv[argv.index("--out") + 1]).resolve() if "--out" in argv else Path.cwd()
    if not (d / "module.json").is_file():
        sys.exit(f"{d}: no module.json")
    typecheck_and_build(d)
    run([sys.executable, str(ROOT / "tools/check_module.py"), str(d)])
    zip_path = pack(d, out)
    print(f"packed {zip_path}")
    return zip_path


if __name__ == "__main__":
    main(sys.argv[1:])
