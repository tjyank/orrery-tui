"""Keep the tests away from the user's real files: settings, caches and added objects all go to a
temporary folder. This runs before any test module imports orrery, which reads these at import."""
import os
import shutil
import tempfile

_home = tempfile.mkdtemp(prefix="orrery-tests-")
os.environ["ORRERY_SETTINGS"] = os.path.join(_home, "settings.json")
os.environ["ORRERY_CACHE"] = os.path.join(_home, "horizons.json")
os.environ["ORRERY_BODIES"] = os.path.join(_home, "bodies.json")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_home, ignore_errors=True)
