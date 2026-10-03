# SPDX-License-Identifier: MIT
# Free to copy as the starting point of your own module (see LICENSES/MIT.txt); delete these two lines.
"""Module template. Copy this folder to modules/<id>/ (no leading "_") to create a module.

The folder name is the module id. Only module.json is required. Full contract:
docs/MODULES.md.

Plain functions run in a worker thread (they may block: downloads, subprocesses…);
`async def` functions run in the service's event loop and must not block. setup,
teardown and on_change callbacks may run in any thread and must return quickly: for
background work, start your own thread in setup() and stop it in teardown().
Helper files next to this one can be imported relatively: `from . import helpers`.
"""

ctx = None


def setup(context):
    """Called once when the module loads (and again if it's re-enabled). context gives:
    - context.settings.get(key=None): settings from module.json, always valid
    - context.settings.set(key, value): validated like the panel's form
    - context.settings.on_change(cb): cb(key, value) when the user changes one in the panel
    - context.data: free-form JsonStore (load() / save(dict) / update(**kw))
    - context.game_data(appid): free-form JsonStore for this module and one game
    - context.game.selected / context.game.running: {"appid", "name", "shortcut"} or None
    - context.on_steam_start(cb): cb() each time Steam starts or restarts (now, if it's up)
    - context.log: this module's logger
    """
    global ctx
    ctx = context
    context.settings.on_change(lambda key, value: context.log.info("setting %s -> %r", key, value))


def teardown():
    """Called when the module is disabled and when the service stops: stop threads,
    timers, subprocesses… Optional."""


def upgrade(context, previous_version):
    """Called before setup() when a different version than last time loads (the
    module was replaced, or Invasor updated): migrate context.data, files… If it
    raises, the module isn't loaded and it's called again next time. Optional."""


def uninstall(context, purge):
    """Called when the user uninstalls the module, after teardown() and before its
    files are deleted: undo what it left outside its folder. purge is True when the
    user also asked to delete its settings and data. Up to 10 seconds; if it fails,
    the module is uninstalled anyway. Steam may not be there. Optional."""


def hello(name="world"):
    return f"{ctx.settings.get('greeting')} {name}"


# Exposed to the UI as ctx.call("hello", {"name": ...}). Results must be JSON-able.
METHODS = {"hello": hello}
