"""Demo's pure helpers: backend.py imports them with `from . import helpers`, and the
tests import them directly."""

# The fan speed each preset sets (Custom leaves it to the user).
PRESET_FAN = {"quiet": 30, "max": 100}
PRESET_LABELS = {"custom": "Custom", "quiet": "Quiet", "max": "Max"}


def preset_fan(preset):
    """The fan speed `preset` imposes, or None if the user chooses it."""
    return PRESET_FAN.get(preset)


def preset_notification(preset, message):
    """(title, body) of the notification for a preset change."""
    return f"Demo: {PRESET_LABELS.get(preset, preset)} preset", message


def profile_section(data):
    """The [profile] table of Demo's TOML file (a dict), whatever the file holds."""
    section = data.get("profile")
    return section if isinstance(section, dict) else {}
