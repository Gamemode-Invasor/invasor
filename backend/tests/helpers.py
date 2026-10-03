"""Shared test helpers: keep tests away from the real ~/.config and Steam dirs."""
import tempfile
from pathlib import Path
from unittest import mock


def temp_dir(test):
    d = tempfile.TemporaryDirectory()
    test.addCleanup(d.cleanup)
    return Path(d.name)


def patch(test, target, value):
    p = mock.patch(target, value)
    p.start()
    test.addCleanup(p.stop)
