"""A screenshot run must never read or write the real credential store.

Store screenshots are public. make_screenshots.py used to stub the store by
replacing load_credentials on its module, which missed every module that had
already bound the name with `from app.core.credential_store import
load_credentials`. app.core.client is one, so ClientManager.reload() — which
MainWindow calls while it is being built — read the real OS keyring during every
--window run and threw the placeholder away before a page was painted.

The stub is now the keyring backend. These tests put a recording stand-in where
the OS vault would be, holding a key that plays a real one, and check nothing
reaches it once the stub is in place. The modules that bind load_credentials by
name are found by scanning app/, not listed by hand, so a new one is covered the
day it is written. Every negative has a positive control showing the same probe
does see the vault when the stub is absent, or done the old way.
"""

import ast
import importlib
import importlib.util
import re
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

REPO_ROOT = Path(__file__).resolve().parent.parent
STAND_IN_KEY = "EZTK_stand_in_for_a_real_key"


def _load_make_screenshots():
    """`packaging/` is not a package, so load the module by path."""
    path = REPO_ROOT / "packaging" / "make_screenshots.py"
    spec = importlib.util.spec_from_file_location("make_screenshots", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_level_bindings(root: Path):
    """(module, bound name) for every module-level import of load_credentials.

    Only module-level imports bind the function once and keep it. An import
    inside a function reads the module attribute each time it runs, so a stub
    applied to the module reached those even before the stub moved.
    """
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "app.core.credential_store":
                for alias in node.names:
                    if alias.name == "load_credentials":
                        module = ".".join(path.relative_to(root.parent).with_suffix("").parts)
                        found.append((module, alias.asname or alias.name))
    return found


BINDINGS = _module_level_bindings(REPO_ROOT / "app")

# Either of these goes around the installed backend: a platform backend class
# constructed directly talks to the real vault, and set_keyring replaces the stub.
_BACKEND_BYPASS = re.compile(r"keyring\.backends|set_keyring")


def _backend_bypasses(root: Path):
    return [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*.py"))
        if _BACKEND_BYPASS.search(path.read_text(encoding="utf-8"))
    ]


class _VaultStandIn(KeyringBackend):
    """Plays the OS vault: holds a key standing in for a real one, records access."""

    # keyring's own discovery considers every viable subclass it has seen.
    viable = False
    priority = 0

    def __init__(self):
        super().__init__()
        self.entries = {}
        self.accesses = []

    def get_password(self, service, username):
        self.accesses.append(("get", username))
        return self.entries.get((service, username))

    def set_password(self, service, username, password):
        self.accesses.append(("set", username))
        self.entries[(service, username)] = password

    def delete_password(self, service, username):
        self.accesses.append(("delete", username))
        self.entries.pop((service, username), None)


@pytest.fixture(scope="module")
def shots():
    return _load_make_screenshots()


@pytest.fixture
def vault():
    from app.core import credential_store

    previous = keyring.get_keyring()
    stand_in = _VaultStandIn()
    keyring.set_keyring(stand_in)
    credential_store.save_credentials(credential_store.Credentials(test_key=STAND_IN_KEY))

    from app.core.client import client_manager

    saved_credentials = client_manager._credentials
    # A stub that patches the module attribute would otherwise outlive its test
    # and hand every later import the placeholder, so later cases pass unearned.
    saved_function = credential_store.load_credentials
    stand_in.accesses.clear()
    try:
        yield stand_in
    finally:
        credential_store.load_credentials = saved_function
        client_manager._credentials = saved_credentials
        keyring.set_keyring(previous)


def test_scanner_finds_the_binding_that_caused_the_hole():
    """Positive control for the scan: a binding known to exist is found."""
    assert ("app.core.client", "load_credentials") in BINDINGS


def test_vault_is_visible_without_the_stub(vault):
    """Positive control: with nothing stubbed, the same probe sees the read."""
    from app.core.client import client_manager

    client_manager.reload()
    assert client_manager.credentials.test_key == STAND_IN_KEY
    assert any(op == "get" for op, _ in vault.accesses)


def test_a_module_level_stub_is_caught(vault, monkeypatch):
    """Positive control for the defect itself, in the shape the old stub had.

    Replacing load_credentials on its module leaves a name bound beforehand
    pointing at the original, and the vault records the read.
    """
    from app.core import credential_store

    bound_beforehand = credential_store.load_credentials
    monkeypatch.setattr(
        credential_store, "load_credentials",
        lambda: credential_store.Credentials(test_key="placeholder"),
    )
    assert bound_beforehand().test_key == STAND_IN_KEY
    assert vault.accesses


@pytest.mark.parametrize("module_name, bound_name", BINDINGS)
def test_no_binding_reaches_the_vault_once_stubbed(vault, shots, module_name, bound_name):
    module = importlib.import_module(module_name)
    bound = getattr(module, bound_name)
    # The case the old stub missed is a name bound to the real function before
    # stubbing. If something earlier left a replacement bound here instead, this
    # case would pass without testing anything.
    assert (bound.__module__, bound.__name__) == ("app.core.credential_store", "load_credentials")
    vault.accesses.clear()

    shots._stub_credentials()
    creds = bound()

    assert creds.test_key == shots.PLACEHOLDER_TEST_KEY
    assert creds.production_key is None
    assert vault.accesses == []


def test_client_manager_reload_stays_on_the_placeholder(vault, shots):
    """The route that leaked: MainWindow calls reload() while it is built."""
    from app.core.client import client_manager

    shots._stub_credentials()
    client_manager.reload()

    assert client_manager.credentials.test_key == shots.PLACEHOLDER_TEST_KEY
    assert vault.accesses == []


def test_writes_never_reach_the_vault_once_stubbed(vault, shots):
    """MainWindow._route_startup and the mode banner both save credentials."""
    from app.core import credential_store

    shots._stub_credentials()
    credential_store.save_credentials(credential_store.Credentials(active_mode="production"))
    credential_store.clear_credentials()

    assert vault.accesses == []
    assert any(STAND_IN_KEY in stored for stored in vault.entries.values())


def test_capture_gate_refuses_without_the_stub(vault, shots):
    with pytest.raises(SystemExit):
        shots._assert_credentials_isolated()


def test_capture_gate_passes_once_stubbed(vault, shots):
    shots._stub_credentials()
    shots._assert_credentials_isolated()


def test_capture_gate_refuses_a_client_manager_holding_other_credentials(vault, shots):
    """The backend being right is not enough; the gate checks what was loaded."""
    from app.core import credential_store
    from app.core.client import client_manager

    shots._stub_credentials()
    client_manager._credentials = credential_store.Credentials(test_key=STAND_IN_KEY)
    with pytest.raises(SystemExit):
        shots._assert_credentials_isolated()


def test_backend_bypass_scan_sees_a_bypass(tmp_path):
    """Positive control for the scan below."""
    (tmp_path / "direct.py").write_text(
        "from keyring.backends.Windows import WinVaultKeyring\n", encoding="utf-8"
    )
    assert _backend_bypasses(tmp_path) == ["direct.py"]


def test_app_never_goes_around_the_installed_keyring_backend():
    """The stub holds only while every credential access uses the installed backend."""
    assert _backend_bypasses(REPO_ROOT / "app") == []
