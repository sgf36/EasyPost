"""Keep the test suite away from the developer's own application data.

app/config.py fixes APP_DATA_DIR once, at import, from EASYPOST_DESKTOP_DATA_DIR
or else the real per-user data directory. Several suites clear tables outright
(`DELETE FROM trackers`, shipments and others), so running the suite on a
machine that also runs the app wiped that machine's local records in both test
and production modes. Importing app.core.client also builds ClientManager at
module level, which reads the OS keyring.

pytest imports this file before it collects any test module beside it, so both
are redirected here, before anything can import app.*:

* The data directory is a new temporary directory for the session. It is set
  unconditionally: a developer who exported the variable for a manual run could
  be pointing it at their real data.
* The keyring backend is an in-memory one. It replaces the backend rather than
  patching keyring functions onto app modules, because a name already bound by
  `from x import f` is not reached by patching x.f — the hole
  packaging/make_screenshots.py had (tests/test_screenshot_credential_isolation.py).

A path computed before this ran cannot be moved, so if app.config resolved
anywhere else the session refuses to start rather than warning.
"""

import os
import shutil
import tempfile
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

SESSION_DATA_DIR = Path(tempfile.mkdtemp(prefix="easypost-tests-")).resolve()


class SessionKeyring(KeyringBackend):
    """The keyring for a test session: in memory, gone when the process exits."""

    # keyring's own backend discovery considers every viable subclass it has
    # seen, and this one must only ever be installed deliberately.
    viable = False
    priority = 0

    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.entries.get((service, username))

    def set_password(self, service, username, password):
        self.entries[(service, username)] = password

    def delete_password(self, service, username):
        if self.entries.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


SESSION_KEYRING = SessionKeyring()

os.environ["EASYPOST_DESKTOP_DATA_DIR"] = str(SESSION_DATA_DIR)
keyring.set_keyring(SESSION_KEYRING)

import app.config  # noqa: E402 — must follow the override it is checked against

if Path(app.config.APP_DATA_DIR).resolve() != SESSION_DATA_DIR:
    # pytest_unconfigure is never registered when this file fails to import.
    shutil.rmtree(SESSION_DATA_DIR, ignore_errors=True)
    raise RuntimeError(
        "Refusing to run the tests: app.config resolved its data directory to "
        f"{app.config.APP_DATA_DIR} instead of the session's temporary directory. "
        "It was imported before tests/conftest.py, so the suite would clear real "
        "application data."
    )


@pytest.fixture(scope="session")
def session_data_dir() -> Path:
    return SESSION_DATA_DIR


@pytest.fixture(scope="session")
def session_keyring() -> SessionKeyring:
    return SESSION_KEYRING


def pytest_unconfigure(config):
    # Best effort: on Windows a SQLite handle still open at exit keeps its file
    # locked, and a leftover directory under the temp root harms nothing.
    shutil.rmtree(SESSION_DATA_DIR, ignore_errors=True)
