"""The suite runs against a throwaway data directory and an in-memory keyring.

tests/conftest.py redirects both before any app module is imported. These tests
observe where the app itself reads and writes — the file SQLite opened, the
settings file written, the backend a credential lands in — rather than trusting
the variable conftest set. Each has a positive control run in a clean
interpreter without the conftest, where the same probe sees the real per-user
directory or a real OS keyring backend, so a check that passes here is not one
that could never fail.
"""

import os
import subprocess
import sys
from pathlib import Path

import keyring
import platformdirs

from app import config

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = Path(platformdirs.user_data_dir(config.APP_DIR_NAME, appauthor=False))


def _inside(path, directory) -> bool:
    return Path(path).resolve().is_relative_to(Path(directory).resolve())


def _clean_interpreter(code: str) -> subprocess.CompletedProcess:
    """Run code in a fresh Python that has not loaded tests/conftest.py.

    The override this session set is removed from the child's environment, or
    the child would inherit the very isolation the control is meant to lack.
    """
    env = dict(os.environ)
    env.pop("EASYPOST_DESKTOP_DATA_DIR", None)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )


def test_the_database_sqlite_opens_is_in_the_session_directory(session_data_dir):
    from app.core.db import db_cursor, init_db

    init_db()
    with db_cursor() as cur:
        opened = {row["name"]: row["file"] for row in cur.execute("PRAGMA database_list")}

    assert _inside(opened["main"], session_data_dir)
    assert not _inside(opened["main"], REAL_DATA_DIR)


def test_settings_are_written_in_the_session_directory(session_data_dir):
    from app.core import settings

    settings.save_settings(settings.load_settings())

    assert (session_data_dir / "settings.json").is_file()
    assert _inside(settings.SETTINGS_PATH, session_data_dir)


def test_a_clean_interpreter_resolves_the_real_data_directory(session_data_dir):
    """Positive control: without the conftest the database path is the user's own."""
    result = _clean_interpreter("import app.config; print(app.config.DATABASE_PATH)")

    assert result.returncode == 0, result.stderr
    resolved = Path(result.stdout.strip())
    assert resolved == REAL_DATA_DIR / "easypost_desktop.sqlite3"
    assert not _inside(resolved, session_data_dir)


def test_credentials_land_in_the_in_memory_keyring(session_keyring):
    from app.core import client, credential_store

    assert keyring.get_keyring() is session_keyring
    assert not type(keyring.get_keyring()).__module__.startswith("keyring.backends")

    before = dict(session_keyring.entries)
    try:
        credential_store.save_credentials(
            credential_store.Credentials(test_key="EZTK_isolation_probe")
        )
        assert any("EZTK_isolation_probe" in v for v in session_keyring.entries.values())
        # app.core.client binds load_credentials by name, the route a patched
        # module attribute once failed to reach.
        assert client.load_credentials().test_key == "EZTK_isolation_probe"
    finally:
        session_keyring.entries.clear()
        session_keyring.entries.update(before)


def test_a_clean_interpreter_gets_an_os_keyring_backend():
    """Positive control: without the conftest keyring picks a platform backend."""
    result = _clean_interpreter("import keyring; print(type(keyring.get_keyring()).__module__)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("keyring.backends")


_RUN_A_HARMLESS_SUITE = (
    "sys.exit(pytest.main(['tests/test_units.py', '-q', '-p', 'no:cacheprovider']))"
)


def test_the_session_refuses_to_start_if_app_config_was_imported_first():
    """A data directory resolved before the conftest ran cannot be moved."""
    result = _clean_interpreter(f"import sys, app.config, pytest; {_RUN_A_HARMLESS_SUITE}")

    assert result.returncode != 0
    assert "Refusing to run the tests" in result.stdout + result.stderr


def test_the_same_session_starts_when_app_config_was_not_imported_first():
    """Positive control for the refusal: the identical run otherwise proceeds."""
    result = _clean_interpreter(f"import sys, pytest; {_RUN_A_HARMLESS_SUITE}")

    assert result.returncode == 0, result.stdout + result.stderr
