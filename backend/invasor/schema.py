"""The module contract: module.json validation and the declarative settings schema.

Pure functions, no I/O, so the same rules serve the service, tools/check_module.py
and the tests. Everything is strict on purpose: an unknown key or a wrong type is an
error with its exact path (e.g. "settings[2].max: must be a number"), never a guess.
"""
import math
import re
from decimal import Decimal

API_VERSION = 1

MANIFEST_KEYS = {"api", "name", "version", "description", "author", "order", "tab", "settings", "forms", "no_qam"}
AUTHOR_MAX = 128
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TEXT_MAX_LENGTH = 256

# Keys each field type accepts besides key/type/label/default/hint.
FIELD_EXTRA = {
    "toggle": set(),
    "checkbox": set(),
    "slider": {"min", "max", "step", "unit"},
    "number": {"min", "max", "step", "unit"},
    "radio": {"options"},
    "select": {"options"},
    "text": {"max_length", "placeholder"},
}
FIELD_COMMON = {"key", "type", "label", "default", "hint", "when", "disabled_when"}
SECTION_KEYS = {"section", "open", "items", "when"}


class SchemaError(ValueError):
    """A module.json that breaks the contract. The message starts with the path."""


class InvalidArgument(ValueError):
    """A caller sent a value the contract doesn't allow (unknown key, wrong type…).
    The API answers 400 and logs one line: the caller's mistake, not a crash."""


class Unavailable(RuntimeError):
    """Something optional isn't there right now (e.g. a SteamClient function, the
    network). The API answers 503 and logs one line; callers fall back."""


def _fail(path, msg):
    raise SchemaError(f"{path}: {msg}" if path else msg)


def _is_number(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # an int too big for a float (e.g. 10**400)
        return False


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _str(data, key, path, required=False, default=None):
    if key not in data:
        if required:
            _fail(f"{path}{key}", "is required")
        return default
    v = data[key]
    if not isinstance(v, str) or (required and not v.strip()):
        _fail(f"{path}{key}", "must be a non-empty string" if required else "must be a string")
    return v


def _decimals(x):
    exp = Decimal(repr(x)).normalize().as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def snap(value, lo, hi, step):
    """Clamp to [lo, hi] and round to the nearest step counted from lo, without float
    noise: snap(0.30000000000000004, 0, 1, 0.1) == 0.3. Ints stay ints."""
    last = math.floor((hi - lo) / step + 1e-9)  # if hi isn't on a step, the last one below it
    n = min(last, max(0, round((value - lo) / step)))
    v = lo + n * step
    places = max(_decimals(step), _decimals(lo))
    v = round(v, places)
    return int(v) if places == 0 and _is_int(lo) and _is_int(step) else v


def _same(a, b):
    """Equality that doesn't confuse True with 1 (options are compared this way)."""
    return type(a) is type(b) and a == b


def _option_value(v):
    return isinstance(v, (str, bool)) or _is_number(v)


def coerce(field, value):
    """Validated, normalized value for `field`, or InvalidArgument explaining why not."""
    t = field["type"]
    key = field["key"]
    if t in ("toggle", "checkbox"):
        if not isinstance(value, bool):
            raise InvalidArgument(f"{key}: expected true/false, got {value!r}")
        return value
    if t in ("slider", "number"):
        if not _is_number(value):
            raise InvalidArgument(f"{key}: expected a number, got {value!r}")
        return snap(value, field["min"], field["max"], field["step"])
    if t in ("radio", "select"):
        for opt in field["options"]:
            if _same(opt["value"], value):
                return opt["value"]
        raise InvalidArgument(f"{key}: {value!r} is not one of the options")
    if t == "text":
        if not isinstance(value, str):
            raise InvalidArgument(f"{key}: expected text, got {value!r}")
        if len(value) > field["max_length"]:
            raise InvalidArgument(f"{key}: longer than {field['max_length']} characters")
        return value
    raise InvalidArgument(f"{key}: unknown type {t!r}")


def _parse_field(raw, path, keys):
    if not isinstance(raw, dict):
        _fail(path, "must be an object")
    t = raw.get("type")
    if t not in FIELD_EXTRA:
        _fail(f"{path}.type", f"must be one of {', '.join(sorted(FIELD_EXTRA))}")
    unknown = set(raw) - FIELD_COMMON - FIELD_EXTRA[t]
    if unknown:
        _fail(path, f"unknown key(s) for {t}: {', '.join(sorted(unknown))}")
    key = _str(raw, "key", f"{path}.", required=True)
    if not KEY_RE.match(key):
        _fail(f"{path}.key", "letters, digits and _ only, not starting with a digit")
    if key in keys:
        _fail(f"{path}.key", f"duplicate key {key!r}")
    keys.add(key)
    field = {
        "key": key,
        "type": t,
        "label": _str(raw, "label", f"{path}.", required=True),
    }
    if "hint" in raw:
        field["hint"] = _str(raw, "hint", f"{path}.")
    for cond in ("when", "disabled_when"):
        if cond in raw:
            field[cond] = raw[cond]  # checked once the whole form is known (_check_when)

    if t in ("slider", "number"):
        for k in ("min", "max"):
            if not _is_number(raw.get(k)):
                _fail(f"{path}.{k}", "must be a number")
            field[k] = raw[k]
        if field["min"] >= field["max"]:
            _fail(f"{path}.max", "must be greater than min")
        step = raw.get("step", 1)
        if not _is_number(step) or step <= 0:
            _fail(f"{path}.step", "must be a positive number")
        field["step"] = step
        if "unit" in raw:
            field["unit"] = _str(raw, "unit", f"{path}.")
    elif t in ("radio", "select"):
        opts = raw.get("options")
        if not isinstance(opts, list) or not opts:
            _fail(f"{path}.options", "must be a non-empty list")
        field["options"] = []
        for j, o in enumerate(opts):
            opath = f"{path}.options[{j}]"
            if not isinstance(o, dict) or set(o) - {"value", "label"}:
                _fail(opath, 'must be {"value": …, "label": "…"}')
            if not _option_value(o.get("value")):
                _fail(f"{opath}.value", "must be a string, number or true/false")
            if any(_same(o["value"], x["value"]) for x in field["options"]):
                _fail(f"{opath}.value", f"duplicate value {o['value']!r}")
            field["options"].append({"value": o["value"], "label": _str(o, "label", f"{opath}.", required=True)})
    elif t == "text":
        n = raw.get("max_length", TEXT_MAX_LENGTH)
        if not _is_int(n) or n <= 0:
            _fail(f"{path}.max_length", "must be a positive integer")
        field["max_length"] = n
        if "placeholder" in raw:
            field["placeholder"] = _str(raw, "placeholder", f"{path}.")

    if "default" not in raw:
        _fail(f"{path}.default", "is required")
    try:
        default = coerce(field, raw["default"])
    except ValueError as e:
        _fail(f"{path}.default", str(e).split(": ", 1)[-1])
    if default != raw["default"]:
        _fail(f"{path}.default", f"must be within min..max and on a step (closest valid: {default})")
    field["default"] = default
    return field


def parse_settings(raw, path="settings"):
    """(tree, fields): the normalized tree (fields and sections, for the UI) and a flat
    {key: field} map (for validation)."""
    if raw is None:
        return [], {}
    if not isinstance(raw, list):
        _fail(path, "must be a list")
    keys = set()
    fields = {}
    tree = []
    for i, item in enumerate(raw):
        ipath = f"{path}[{i}]"
        if isinstance(item, dict) and "section" in item:
            unknown = set(item) - SECTION_KEYS
            if unknown:
                _fail(ipath, f"unknown key(s) for a section: {', '.join(sorted(unknown))}")
            title = _str(item, "section", f"{ipath}.", required=True)
            is_open = item.get("open", True)
            if not isinstance(is_open, bool):
                _fail(f"{ipath}.open", "must be true or false")
            items = item.get("items")
            if not isinstance(items, list) or not items:
                _fail(f"{ipath}.items", "must be a non-empty list of fields")
            children = []
            for j, sub in enumerate(items):
                if isinstance(sub, dict) and "section" in sub:
                    _fail(f"{ipath}.items[{j}]", "sections can't be nested")
                f = _parse_field(sub, f"{ipath}.items[{j}]", keys)
                fields[f["key"]] = f
                children.append(f)
            section = {"section": title, "open": is_open, "items": children}
            if "when" in item:
                section["when"] = item["when"]  # disabled_when is for fields only (SECTION_KEYS)
            tree.append(section)
        else:
            f = _parse_field(item, ipath, keys)
            fields[f["key"]] = f
            tree.append(f)
    for i, item in enumerate(tree):
        ipath = f"{path}[{i}]"
        if "section" in item:
            _check_when(item, ipath, fields)
            for j, sub in enumerate(item["items"]):
                _check_when(sub, f"{ipath}.items[{j}]", fields)
        else:
            _check_when(item, ipath, fields)
    return tree, fields


def _check_when(item, path, fields):
    """Conditions on other fields of the same form, view only (hidden or disabled fields
    keep their values and are validated as usual):
      `when`: {key: value, …}           shown only while every key has that value;
      `disabled_when`: {key: value, …}  (fields only) disabled while every key has it.
    A value may be a list: any of those values matches."""
    for cond in ("when", "disabled_when"):
        if cond in item:
            item[cond] = _condition(item, item[cond], f"{path}.{cond}", fields)


def _condition(item, when, path, fields):
    if not isinstance(when, dict) or not when:
        _fail(path, 'must be a non-empty object: {"key": value}')
    out = {}
    for key, value in when.items():
        if key not in fields:
            _fail(f"{path}.{key}", "isn't a field of this form")
        if key == item.get("key"):
            _fail(f"{path}.{key}", "a field can't depend on itself")
        values = value if isinstance(value, list) else [value]
        if not values:
            _fail(f"{path}.{key}", "an empty list matches nothing")
        checked = []
        for v in values:
            try:
                c = coerce(fields[key], v)
            except InvalidArgument as e:
                _fail(f"{path}.{key}", str(e).split(": ", 1)[-1])
            if not _same(c, v) and not (_is_number(v) and c == v):
                _fail(f"{path}.{key}", f"{v!r} isn't a value {key} can take")
            checked.append(c)
        out[key] = checked if isinstance(value, list) else checked[0]
    return out


def author_name(text):
    """What the UI shows of module.json "author": "Name <email>" -> "Name"."""
    text = text.strip()
    m = re.fullmatch(r"(.*?)\s*<[^<>]*>", text)
    return (m.group(1) if m and m.group(1) else text).strip()


def parse_manifest(data, module_id):
    """Normalized module.json, or SchemaError. `fields` is the flat settings map."""
    if not ID_RE.match(module_id):
        _fail("", f"invalid module id {module_id!r}: use lowercase letters, digits, - and _")
    if not isinstance(data, dict):
        _fail("", "module.json must be a JSON object")
    unknown = set(data) - MANIFEST_KEYS
    if unknown:
        _fail("", f"unknown key(s): {', '.join(sorted(unknown))}")
    api = data.get("api")
    if api != API_VERSION or not _is_int(api):
        _fail("api", f"must be {API_VERSION} (this Invasor implements module API {API_VERSION}), got {api!r}")
    order = data.get("order", 100)
    if not _is_int(order):
        _fail("order", "must be an integer")
    no_qam = data.get("no_qam", False)
    if not isinstance(no_qam, bool):
        _fail("no_qam", "must be true or false")
    tree, fields = parse_settings(data.get("settings"))
    forms, form_fields = parse_forms(data.get("forms"))
    name = _str(data, "name", "", required=True)
    author = _str(data, "author", "", default="")
    if len(author) > AUTHOR_MAX or "\n" in author or "\r" in author:
        _fail("author", f"must be one line of up to {AUTHOR_MAX} characters")
    return {
        "id": module_id,
        "api": api,
        "name": name,
        "version": _str(data, "version", "", required=True),
        "description": _str(data, "description", "", default=""),
        # Free text, e.g. "Name" or "Name <email>"; the UI shows author_name (no email).
        "author": author,
        "author_name": author_name(author),
        "order": order,
        "tab": _str(data, "tab", "", default=None) or name,
        "settings": tree,
        "fields": fields,
        # True: not shown in the Quick Access (···) panel, only in the library's.
        "no_qam": no_qam,
        "forms": forms,
        "form_fields": form_fields,
    }


def parse_forms(raw):
    """({name: tree}, {name: fields}): named forms with the same field rules as
    "settings", for values the module stores itself (e.g. another program's config)."""
    if raw is None:
        return {}, {}
    if not isinstance(raw, dict):
        _fail("forms", 'must be an object: {"name": [fields…]}')
    trees, fields = {}, {}
    for name, items in raw.items():
        if not KEY_RE.match(name):
            _fail(f"forms.{name}", "form names: letters, digits and _ only, not starting with a digit")
        trees[name], fields[name] = parse_settings(items, f"forms.{name}")
        if not fields[name]:
            _fail(f"forms.{name}", "a form needs at least one field")
    return trees, fields


def clean(fields, data, log=None):
    """Every setting with a valid value: stored ones that pass, defaults for the rest.
    Keys no longer in the schema are dropped."""
    data = data if isinstance(data, dict) else {}
    out = {}
    for key, field in fields.items():
        if key in data:
            try:
                out[key] = coerce(field, data[key])
                continue
            except ValueError as e:
                if log:
                    log.warning("invalid stored setting, using default: %s", e)
        out[key] = field["default"]
    return out
