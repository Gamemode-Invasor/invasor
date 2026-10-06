"""invasor-installation.sh, run for real in a sandbox (fake systemctl, fake /health, temp HOME)."""

import http.server
import os
import shutil
import stat
import subprocess
import threading
import unittest
from pathlib import Path

from .helpers import temp_dir

SCRIPT = Path(__file__).resolve().parents[2] / "invasor-installation.sh"
PYTHON = "/usr/bin/python3"

FAKE_SYSTEMCTL = """#!/bin/sh
echo "$@" >> "$FAKE_LOG"
case "$*" in
  *enable*) grep -q FAIL_ENABLE "$XDG_DATA_HOME/invasor/backend/invasor/__init__.py" && exit 1 ;;
  *is-active*) grep -q BROKEN "$XDG_DATA_HOME/invasor/backend/invasor/__init__.py" && exit 3 ;;
esac
exit 0
"""

CONFIG_PY = "def load():\n    return {{'api_port': {port}}}\n"


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@unittest.skipUnless(
    os.name == "posix" and shutil.which("bash") and os.path.exists(PYTHON) and os.geteuid() != 0,
    "needs POSIX, bash, /usr/bin/python3 and a non-root user",
)
class Installer(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        self.home = self.root / "home"
        (self.home / ".steam/steam").mkdir(parents=True)
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.dest = self.data / "invasor"
        self.unit = self.config / "systemd/user/invasor.service"
        self.log = self.root / "systemctl.log"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        # Never the real service's port: a developer's running Invasor would answer it.
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = {
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CONFIG_HOME": str(self.config),
            "PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
            "FAKE_LOG": str(self.log),
            "INVASOR_HEALTH_TRIES": "3",
        }

    def release(self, version, broken=False, marker=""):
        d = self.root / ("release-" + version + ("-broken" if broken else "") + ("-marked" if marker else ""))
        pkg = d / "backend/invasor"
        pkg.mkdir(parents=True)
        pkg.joinpath("__init__.py").write_text('__version__ = "%s"\n%s%s' % (version, "# BROKEN\n" if broken else "", marker))
        pkg.joinpath("config.py").write_text(CONFIG_PY.format(port=self.port))
        (d / "frontend/dist").mkdir(parents=True)
        (d / "frontend/dist/invasor.js").write_text("// ui\n")
        (d / "modules/demo").mkdir(parents=True)  # a release always bundles a module
        (d / "modules/demo/module.json").write_text("{}\n")
        shutil.copy(SCRIPT, d / "invasor-installation.sh")
        return d

    def install(self, release):
        return subprocess.run(
            ["bash", str(release / "invasor-installation.sh"), "--install"],
            env=self.env, capture_output=True, text=True, timeout=60,
        )

    def installed_version(self):
        text = (self.dest / "backend/invasor/__init__.py").read_text()
        return text.split('"')[1]

    def restarts(self):
        return sum(1 for line in self.log.read_text().splitlines() if "restart" in line)

    def leftovers(self):
        return sorted(p.name for p in self.dest.iterdir() if p.name.startswith((".old-", ".staging")))

    def test_update(self):
        self.assertEqual(self.install(self.release("1.0.0")).returncode, 0)
        r = self.install(self.release("2.0.0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.installed_version(), "2.0.0")
        self.assertEqual(self.leftovers(), [])

    def test_the_installer_is_kept_next_to_the_install(self):
        # ⚙ Settings › Manage Invasor uninstalls with it.
        self.assertEqual(self.install(self.release("1.0.0")).returncode, 0)
        self.assertEqual((self.dest / "invasor-installation.sh").read_text(), SCRIPT.read_text())

    def test_a_broken_update_goes_back(self):
        self.assertEqual(self.install(self.release("1.0.0")).returncode, 0)
        unit_before = self.unit.read_text()
        r = self.install(self.release("2.0.0", broken=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.installed_version(), "1.0.0")
        self.assertEqual(self.unit.read_text(), unit_before)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.restarts(), 3)  # first install, the broken update, the way back
        self.assertIn("Going back to Invasor 1.0.0", r.stderr)

    def test_a_failure_before_the_health_check_goes_back_too(self):
        # systemctl enable fails: with `set -e` alone the script used to stop right there, half swapped.
        self.assertEqual(self.install(self.release("1.0.0")).returncode, 0)
        r = self.install(self.release("2.0.0", marker="# FAIL_ENABLE\n"))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.installed_version(), "1.0.0")
        self.assertEqual(self.leftovers(), [])
        self.assertIn("Going back to Invasor 1.0.0", r.stderr)

    def test_an_interrupted_install_leaves_a_backup_that_the_next_one_keeps(self):
        self.assertEqual(self.install(self.release("1.0.0")).returncode, 0)
        # What a run killed between the two moves leaves: the old backend aside, no new one yet.
        (self.dest / "backend").rename(self.dest / ".old-backend")
        r = self.install(self.release("2.0.0", broken=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("interrupted", r.stderr)
        self.assertEqual(self.installed_version(), "1.0.0")  # the backup survived both attempts
        self.assertEqual(self.leftovers(), [])

    def test_the_installer_makes_the_cef_flag_and_says_to_restart_steam(self):
        flag = self.home / ".steam/steam/.cef-enable-remote-debugging"
        r = self.install(self.release("1.0.0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(flag.exists())
        self.assertTrue((self.dest / ".cef-flag-created").exists())  # ours: --uninstall may remove it
        self.assertIn("Restart Steam once", r.stdout)

    def test_someone_elses_cef_flag_is_not_ours(self):
        flag = self.home / ".steam/steam/.cef-enable-remote-debugging"
        flag.touch()
        r = self.install(self.release("1.0.0"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.dest / ".cef-flag-created").exists())
        self.assertNotIn("Restart Steam once", r.stdout)

    def test_a_broken_first_install_has_nothing_to_go_back_to(self):
        r = self.install(self.release("1.0.0", broken=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Going back", r.stderr)


if __name__ == "__main__":
    unittest.main()
