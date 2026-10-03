"""Import a module's backend in a separate process to see that it loads, without
calling setup(): `python3 -m invasor.checkmod <module dir>`. Prints a JSON line
{"ok": bool, "error": str|None} and exits 0/1. Used before installing a module, so
a broken (or exit()-ing) backend never runs inside the service."""
import importlib.util
import json
import sys
from pathlib import Path


def check(d):
    backend = d / "backend.py"
    if not backend.exists():
        return None
    pkg = "invasor_check_module"
    spec = importlib.util.spec_from_file_location(pkg, backend, submodule_search_locations=[str(d)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[pkg] = module
    spec.loader.exec_module(module)
    methods = getattr(module, "METHODS", {})
    if not isinstance(methods, dict) or not all(isinstance(k, str) and callable(v) for k, v in methods.items()):
        return "METHODS must be a dict of {name: function}"
    for hook in ("setup", "teardown"):
        if hasattr(module, hook) and not callable(getattr(module, hook)):
            return f"{hook} must be a function"
    return None


def main():
    sys.dont_write_bytecode = True
    try:
        error = check(Path(sys.argv[1]))
    except BaseException as e:  # noqa: BLE001 - anything the import raises, exit() included
        error = f"backend.py doesn't import: {type(e).__name__}: {e}"
    print(json.dumps({"ok": error is None, "error": error}))
    sys.stdout.flush()
    raise SystemExit(0 if error is None else 1)


if __name__ == "__main__":
    main()
