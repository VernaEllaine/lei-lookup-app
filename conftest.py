"""pytest configuration: isolate the SQLite database to a temp directory."""
import threading

import pytest


@pytest.fixture(autouse=True, scope="session")
def isolated_database(tmp_path_factory):
    """Redirect backend.database to a temp SQLite file for the test session.

    This avoids touching the real lei_lookup.db and keeps test runs clean.
    The module-level _DB_PATH string is patched *after* import, and _local is
    reset so that _get_conn() opens a fresh connection to the temp path.
    """
    import backend.database as db_mod

    tmpdir = tmp_path_factory.mktemp("db")
    db_path = str(tmpdir / "lei_lookup_test.db")

    original_path = db_mod._DB_PATH
    original_local = db_mod._local

    db_mod._DB_PATH = db_path
    db_mod._local = threading.local()
    db_mod.init_db()

    yield

    db_mod._DB_PATH = original_path
    db_mod._local = original_local
