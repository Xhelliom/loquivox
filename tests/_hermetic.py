"""Imported first by every test: defaults plus the test's own fixtures, never the machine's.

``Path.home()`` is read at import time by config, settings, secrets and the
log, so pointing HOME/XDG at an empty directory *before* ``loquivox`` is
imported is the one place that isolates them all.
"""
import atexit
import os
import shutil
import tempfile

HOME = tempfile.mkdtemp(prefix="loquivox-test-home-")
atexit.register(shutil.rmtree, HOME, ignore_errors=True)
os.environ.update(HOME=HOME, XDG_CONFIG_HOME=f"{HOME}/.config",
                  XDG_STATE_HOME=f"{HOME}/.local/state", XDG_DATA_HOME=f"{HOME}/.local/share",
                  XDG_CACHE_HOME=f"{HOME}/.cache")
os.environ.pop("LOQUIVOX_LOG_FILE", None)
