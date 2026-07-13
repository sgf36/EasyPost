from app.core.credential_store import Credentials, clear_credentials, load_credentials, save_credentials


class _FakeKeyring:
    """In-memory stand-in for the OS keyring so tests never touch the real
    Windows Credential Manager / macOS Keychain / Secret Service."""

    def __init__(self):
        self._store = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError()
        del self._store[(service, username)]


def _install_fake_keyring(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr("app.core.credential_store.keyring.set_password", fake.set_password)
    monkeypatch.setattr("app.core.credential_store.keyring.get_password", fake.get_password)
    monkeypatch.setattr("app.core.credential_store.keyring.delete_password", fake.delete_password)
    return fake


def test_round_trip(monkeypatch):
    _install_fake_keyring(monkeypatch)

    save_credentials(Credentials(test_key="test_abc", production_key=None, active_mode="test"))
    loaded = load_credentials()

    assert loaded.test_key == "test_abc"
    assert loaded.production_key is None
    assert loaded.active_key() == "test_abc"


def test_missing_entry_returns_empty_credentials(monkeypatch):
    _install_fake_keyring(monkeypatch)

    creds = load_credentials()

    assert creds.test_key is None
    assert creds.production_key is None


def test_clear_removes_entry(monkeypatch):
    _install_fake_keyring(monkeypatch)

    save_credentials(Credentials(test_key="test_abc"))
    assert load_credentials().test_key == "test_abc"

    clear_credentials()
    assert load_credentials().test_key is None


def test_clear_when_nothing_stored_does_not_raise(monkeypatch):
    _install_fake_keyring(monkeypatch)

    clear_credentials()  # should not raise even though nothing was ever saved
