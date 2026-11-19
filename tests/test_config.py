import stat

import pytest

from dropbox_organizer import config


def test_app_key_from_env(monkeypatch):
    monkeypatch.setenv("DROPBOX_APP_KEY", " abc123 ")
    assert config.get_app_key() == "abc123"


def test_missing_app_key(monkeypatch):
    monkeypatch.delenv("DROPBOX_APP_KEY", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_app_key()


def test_default_token_path(monkeypatch, tmp_path):
    monkeypatch.delenv(config.CONFIG_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.token_path() == tmp_path / ".config" / "dropbox_organizer" / "token"


def test_token_saved_with_mode_600(tmp_path):
    path = tmp_path / "cfg" / "token"
    path.parent.mkdir()
    path.write_text("old")
    path.chmod(0o644)
    config.save_refresh_token("secret", path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert config.load_refresh_token(path) == "secret"


def test_loose_token_permissions_tightened(tmp_path):
    path = tmp_path / "token"
    path.write_text("secret\n")
    path.chmod(0o644)
    assert config.load_refresh_token(path) == "secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_missing_token(tmp_path):
    with pytest.raises(config.ConfigError, match="auth"):
        config.load_refresh_token(tmp_path / "nope")
