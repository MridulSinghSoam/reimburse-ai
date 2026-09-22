"""
conftest.py - Test setup. Runs before any test.
We remove all Azure keys so tests use the local fallbacks (free, fast, no internet),
and use a temporary database + upload folder.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LOCAL_UPLOAD_DIR"] = f"{_tmp}/uploads"
for key in ["DOCINTEL_ENDPOINT", "DOCINTEL_KEY", "AOAI_ENDPOINT", "AOAI_KEY", "STORAGE_CONNECTION_STRING"]:
    os.environ[key] = ""
