"""Where ShareL keeps things. Honors XDG_* so a test session never touches the real home."""
from __future__ import annotations

import os
from pathlib import Path

APP_ID = "app.sharel.ShareL"
APP_NAME = "ShareL"


def _xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v else Path.home() / default


def config_dir() -> Path:
    override = os.environ.get("SHAREL_CONFIG_DIR")
    return Path(override) if override else _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def data_dir() -> Path:
    override = os.environ.get("SHAREL_DATA_DIR")
    return Path(override) if override else _xdg("XDG_DATA_HOME", ".local/share") / APP_NAME


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_NAME


def pictures_dir() -> Path:
    override = os.environ.get("SHAREL_PICTURES_DIR")
    if override:
        return Path(override)
    try:
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        p = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if p:
            return Path(p)
    except (ImportError, ValueError):
        pass
    return Path.home() / "Pictures"


def default_screenshots_folder() -> Path:
    return pictures_dir() / APP_NAME


def history_path() -> Path:
    return data_dir() / "History.json"


def application_config_path() -> Path:
    return config_dir() / "ApplicationConfig.json"


def hotkeys_config_path() -> Path:
    return config_dir() / "HotkeysConfig.json"


def uploaders_config_path() -> Path:
    return config_dir() / "UploadersConfig.json"


def log_dir() -> Path:
    return data_dir() / "Logs"


def tools_dir() -> Path:
    return data_dir() / "Tools"


def package_data_dir() -> Path:
    """data/ in a source checkout; ~/.local/share/sharel after install.sh; /usr/share/sharel from the .deb."""
    here = Path(__file__).resolve()
    user = _xdg("XDG_DATA_HOME", ".local/share") / "sharel"
    for cand in (here.parents[3] / "data", user, Path("/usr/share/sharel"), Path("/usr/local/share/sharel")):
        if (cand / "stickers").is_dir():
            return cand
    return here.parents[3] / "data"
