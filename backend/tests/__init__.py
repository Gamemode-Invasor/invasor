"""The backend's tests.

Expected failures (a module that raises on purpose, a refused request…) are logged by the
code under test. With no handler Python prints those to stderr: a NullHandler keeps them
off the test run's output, and assertLogs still sees them.
"""
import logging
import warnings

logging.getLogger("invasor").addHandler(logging.NullHandler())

# An HTTPError made without a body keeps a temporary file that Python reports when it's collected.
warnings.filterwarnings("ignore", category=ResourceWarning, message="Implicitly cleaning up <HTTPError")
