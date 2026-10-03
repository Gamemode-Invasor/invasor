#!/usr/bin/env python3
"""Build, test and pack Invasor (core + bundled modules) for other machines:

    python3 tools/pack_release.py [--out <dir>] [--skip-tests]

Steps:
1. build the frontend (core and every module's ui.ts), from a clean `npm ci`;
2. run the backend and frontend tests and tools/check_module.py on each module;
3. pack invasor-<version>/ as invasor-<version>.tar.gz (+ .sha256) with only what
   the installer needs: invasor-installation.sh, README*/LICENSE*, backend/invasor,
   frontend/dist/invasor.js and each module as tools/pack_module.py would zip it.

The target machine needs no Node: extract it and run ./invasor-installation.sh.
"""
import hashlib
import re
import subprocess
import sys
import tarfile
from pathlib import Path

from pack_module import files_to_pack, run

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
sys.dont_write_bytecode = True


def version():
    m = re.search(r'^__version__ = "(.*)"$', (ROOT / "backend/invasor/__init__.py").read_text(), re.M)
    if not m:
        sys.exit("no __version__ in backend/invasor/__init__.py")
    return m.group(1)


def bundled_modules():
    # Same rule as the installer's stage(): templates and hidden folders are never loaded.
    for d in sorted((ROOT / "modules").iterdir()):
        if d.is_dir() and not d.name.startswith(("_", ".")):
            yield d


def build_and_test(skip_tests):
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
    run(["npm", "run", "build"], cwd=FRONTEND)
    if skip_tests:
        return
    run([sys.executable, "-m", "unittest", "discover", "-s", "backend/tests", "-t", "backend"], cwd=ROOT)
    run(["npm", "run", "typecheck"], cwd=FRONTEND)
    run(["npm", "test"], cwd=FRONTEND)
    run([sys.executable, str(ROOT / "tools/check_module.py"), *map(str, bundled_modules())])


def files_to_release():
    """(source, path inside the release folder) for everything that ships."""
    yield ROOT / "invasor-installation.sh", Path("invasor-installation.sh")
    for p in sorted(ROOT.iterdir()):
        if p.is_file() and p.stem.split(".")[0].upper() in ("README", "LICENSE", "COPYING"):
            yield p, Path(p.name)
    core = ROOT / "backend/invasor"
    for p in sorted(core.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p, Path("backend/invasor") / p.relative_to(core)
    yield FRONTEND / "dist/invasor.js", Path("frontend/dist/invasor.js")
    for d in bundled_modules():
        for p, rel in files_to_pack(d):
            yield p, Path("modules", d.name) / rel


def pack(out):
    name = f"invasor-{version()}"
    tar_path = out / f"{name}.tar.gz"

    def normalize(ti):
        # No local user/group in the archive; the installer stays executable.
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.mode = 0o755 if ti.isdir() or ti.name.endswith(".sh") else 0o644
        return ti

    with tarfile.open(tar_path, "w:gz") as tar:
        for src, rel in files_to_release():
            if not src.is_file():
                sys.exit(f"missing {src}")
            tar.add(src, f"{name}/{rel.as_posix()}", filter=normalize)
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    (out / f"{tar_path.name}.sha256").write_text(f"{digest}  {tar_path.name}\n")
    return tar_path


def main(argv):
    if "-h" in argv or "--help" in argv:
        sys.exit(__doc__)
    out = Path(argv[argv.index("--out") + 1]).resolve() if "--out" in argv else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    build_and_test("--skip-tests" in argv)
    tar_path = pack(out)
    print(f"packed {tar_path}")
    return tar_path


if __name__ == "__main__":
    main(sys.argv[1:])
