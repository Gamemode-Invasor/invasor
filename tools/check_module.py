#!/usr/bin/env python3
"""Checks module folders against the module contract (docs/MODULES.md), before
installing or distributing them:

    python3 tools/check_module.py modules/demo modules/_example

- module.json: valid, with the same rules the service applies (backend/invasor/schema.py);
- ui.ts: built (dist/ui.js exists and isn't older than ui.ts);
- backend.py: imports, METHODS is a {name: function} dict, setup/teardown callable.
  setup() isn't called: nothing of the module runs besides its top-level code;
- tests/: if the module has them, they pass (python3 -m unittest discover -s <module>).

Exit status 0 when every module passes.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True  # leave no __pycache__ in the modules checked
sys.path.insert(0, str(ROOT / "backend"))

from invasor import schema  # noqa: E402


def check(d: Path):
    errors, warnings = [], []
    # Templates ("_name") are checked with the id they'd have once copied.
    mid = d.name.lstrip("_")
    try:
        manifest = schema.parse_manifest(json.loads((d / "module.json").read_text()), mid)
    except FileNotFoundError:
        errors.append("module.json is missing")
        manifest = None
    except ValueError as e:
        errors.append(f"module.json: {e}")
        manifest = None

    ui_ts, ui_js = d / "ui.ts", d / "dist" / "ui.js"
    if ui_ts.exists():
        if d.name.startswith("_"):
            pass  # templates are never built
        elif not ui_js.exists():
            errors.append("ui.ts isn't built: run `npm run build` in frontend/")
        elif ui_js.stat().st_mtime < ui_ts.stat().st_mtime:
            warnings.append("dist/ui.js is older than ui.ts: rebuild")
    elif not ui_js.exists() and manifest is not None and not manifest["settings"]:
        warnings.append("no ui.ts and no settings: the module won't have a tab (backend only)")

    backend = d / "backend.py"
    if backend.exists():
        try:
            # Loaded as a package, like the service does, so relative imports work.
            pkg = f"check_{mid}"
            spec = importlib.util.spec_from_file_location(pkg, backend, submodule_search_locations=[str(d)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[pkg] = module
            spec.loader.exec_module(module)
            methods = getattr(module, "METHODS", {})
            if not isinstance(methods, dict) or not all(isinstance(k, str) and callable(v) for k, v in methods.items()):
                errors.append("backend.py: METHODS must be a dict of {name: function}")
            for hook in ("setup", "teardown"):
                if hasattr(module, hook) and not callable(getattr(module, hook)):
                    errors.append(f"backend.py: {hook} must be a function")
        except Exception as e:  # noqa: BLE001 - report anything the import raises
            errors.append(f"backend.py doesn't import: {type(e).__name__}: {e}")

    if (d / "tests").is_dir():
        # The module's tests find this core through INVASOR_CORE (see docs/MODULES.md).
        env = {**__import__("os").environ, "INVASOR_CORE": str(ROOT)}
        run = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(d)],
                             capture_output=True, text=True, env=env)
        summary = (run.stderr.strip().splitlines() or ["?"])[-1]
        if run.returncode != 0:
            errors.append(f"tests fail: {summary}\n{run.stderr[-2000:]}")
    return manifest, errors, warnings


def main(paths):
    if not paths:
        sys.exit(__doc__)
    failed = 0
    for p in map(Path, paths):
        manifest, errors, warnings = check(p)
        title = f"{manifest['name']} {manifest['version']}" if manifest else p.name
        print(f"{'FAIL' if errors else 'OK  '} {p}  ({title})")
        for e in errors:
            print(f"     error: {e}")
        for w in warnings:
            print(f"     warning: {w}")
        failed += bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
