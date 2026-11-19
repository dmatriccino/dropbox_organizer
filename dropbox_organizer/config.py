"""Configuration: app key from the environment, refresh token on local disk."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

APP_KEY_ENV = "DROPBOX_APP_KEY"
CONFIG_DIR_ENV = "DROPBOX_ORGANIZER_CONFIG_DIR"
DB_PATH_ENV = "DROPBOX_ORGANIZER_DB"
REPORTS_DIR_ENV = "DROPBOX_ORGANIZER_REPORTS_DIR"

DEFAULT_DB_PATH = Path("data/dropbox_scan.sqlite")
DEFAULT_REPORTS_DIR = Path("reports")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "dropbox_organizer"


def token_path() -> Path:
    return config_dir() / "token"


def get_app_key() -> str:
    key = os.environ.get(APP_KEY_ENV, "").strip()
    if not key:
        raise ConfigError(
            f"{APP_KEY_ENV} is not set. Create a Dropbox app (see README) and export its app key."
        )
    return key


def save_refresh_token(token: str, path: Path | None = None) -> Path:
    """Write the refresh token with mode 0600 (directory 0700)."""
    path = path or token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:  # pragma: no cover - e.g. a bind mount we don't own
        log.warning("Could not chmod 700 %s", path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token.strip() + "\n")
    # O_CREAT's mode is ignored if the file already existed; enforce it.
    path.chmod(0o600)
    return path


def load_refresh_token(path: Path | None = None) -> str:
    path = path or token_path()
    if not path.exists():
        raise ConfigError(f"No refresh token at {path}. Run `python -m dropbox_organizer auth`.")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning("Token file %s has mode %o; tightening to 600.", path, mode)
        path.chmod(0o600)
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ConfigError(f"Token file {path} is empty. Re-run `auth`.")
    return token
