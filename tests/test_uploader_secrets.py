"""Secret fields (API keys, passwords, OAuth tokens) go to the keyring, the JSON keeps placeholders."""
import json
from dataclasses import dataclass, field

from sharel.uploaders import config as ucfg
from sharel.uploaders.registry import OAuth2Info, setting


@dataclass
class Acct:
    host: str = "h"
    password: str = setting("", secret=True)


@dataclass
class Svc:
    api_key: str = setting("", secret=True)
    name: str = "visible"
    oauth2: OAuth2Info = field(default_factory=OAuth2Info)
    accounts: list[Acct] = field(default_factory=list)


class FakeSecret:
    COLLECTION_DEFAULT = "default"
    store: dict = {}

    @classmethod
    def password_store_sync(cls, _schema, attrs, _coll, _label, value, _cancel):
        cls.store[(attrs["service"], attrs["field"])] = value

    @classmethod
    def password_lookup_sync(cls, _schema, attrs, _cancel):
        return cls.store.get((attrs["service"], attrs["field"]))


def test_secrets_round_trip_through_keyring(monkeypatch, store):
    FakeSecret.store = {}
    monkeypatch.setattr(ucfg, "_secret", lambda: (FakeSecret, object()))
    cfg = store.uploaders
    s = cfg.settings_for("file/X", Svc)
    s.api_key = "K123"
    s.oauth2.access_token = "AT"
    s.oauth2.refresh_token = "RT"
    s.accounts = [Acct(password="pw0"), Acct(password="pw1")]
    store.save_uploaders()
    on_disk = json.loads((store.directory / "UploadersConfig.json").read_text())["services"]["file/X"]
    assert on_disk["api_key"] == ucfg.SECRET_MARK and on_disk["name"] == "visible"
    assert on_disk["oauth2"]["access_token"] == ucfg.SECRET_MARK
    assert [a["password"] for a in on_disk["accounts"]] == [ucfg.SECRET_MARK] * 2
    assert FakeSecret.store[("file/X", "oauth2.refresh_token")] == "RT"
    assert FakeSecret.store[("file/X", "accounts.1.password")] == "pw1"
    from sharel.core.config import ConfigStore
    again = ConfigStore(store.directory).uploaders.settings_for("file/X", Svc)
    assert again.api_key == "K123" and again.oauth2.access_token == "AT"
    assert [a.password for a in again.accounts] == ["pw0", "pw1"]


def test_without_keyring_json_keeps_values(store):
    s = store.uploaders.settings_for("file/Y", Svc)
    s.api_key = "plain"
    store.save_uploaders()
    on_disk = json.loads((store.directory / "UploadersConfig.json").read_text())["services"]["file/Y"]
    assert on_disk["api_key"] == "plain"


def test_keyring_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SHAREL_NO_KEYRING", "1")
    assert ucfg.keyring_available() is False
    assert ucfg._secret() == (None, None)
