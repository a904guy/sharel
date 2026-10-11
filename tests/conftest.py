"""Every test runs with private config/data/pictures dirs: nothing touches the real home."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    for var, sub in (("SHAREL_CONFIG_DIR", "config"), ("SHAREL_DATA_DIR", "data"),
                     ("SHAREL_PICTURES_DIR", "pictures"), ("XDG_CACHE_HOME", "cache")):
        d = tmp_path / sub
        d.mkdir()
        monkeypatch.setenv(var, str(d))
    yield tmp_path


@pytest.fixture
def store(tmp_path):
    from sharel.core.config import ConfigStore
    return ConfigStore(Path(os.environ["SHAREL_CONFIG_DIR"]))
