"""Every test runs with private config/data/pictures dirs: nothing touches the real home."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import sandbox

pytest_plugins = ["pytester"]

sandbox.start()
# Fail closed: no test may run unless the sandbox really is in place.
assert os.environ.get("SHAREL_UNIT_SANDBOX") == "1", "the unit sandbox did not start"
assert "sharel-unit-" in os.environ.get("DBUS_SESSION_BUS_ADDRESS", "") or \
    os.environ.get("DBUS_SESSION_BUS_ADDRESS") == "disabled:", "not on the sandbox's private bus"
_run = os.environ.get("XDG_RUNTIME_DIR", "")
assert "sharel-unit-" in _run, "not in the sandbox's runtime dir"
assert "DISPLAY" not in os.environ, "an X display is reachable"
assert "WAYLAND_DISPLAY" not in os.environ or (
    "/" not in os.environ["WAYLAND_DISPLAY"] and os.path.exists(os.path.join(_run, os.environ["WAYLAND_DISPLAY"]))
), "the Wayland display is not the sandbox's headless one"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    # Never write test secrets into the user's real keyring.
    monkeypatch.setenv("SHAREL_NO_KEYRING", "1")
    for var, sub in (("SHAREL_CONFIG_DIR", "config"), ("SHAREL_DATA_DIR", "data"),
                     ("SHAREL_PICTURES_DIR", "pictures"), ("XDG_CACHE_HOME", "cache")):
        d = tmp_path / sub
        d.mkdir()
        monkeypatch.setenv(var, str(d))
    yield tmp_path


@pytest.fixture(autouse=True)
def no_desktop_launches(request, monkeypatch):
    """See tests/launchguard.py: nothing opens on the real desktop. e2e tests run apps in their own
    isolated shell and are left alone."""
    if request.node.get_closest_marker("e2e"):
        yield None
        return
    import launchguard
    rec = launchguard.Recorder()
    launchguard.install(monkeypatch, rec)
    request.node._launch_recorder = rec
    yield rec
    if rec and not rec.allowed:
        pytest.fail(f"launched on the desktop without the `launched` fixture: {list(rec)}", pytrace=False)


@pytest.fixture
def launched(request, no_desktop_launches):
    """Opt in to launches: they are recorded (not performed); assert on the returned list."""
    no_desktop_launches.allowed = True
    return no_desktop_launches


@pytest.fixture
def store(tmp_path):
    from sharel.core.config import ConfigStore
    return ConfigStore(Path(os.environ["SHAREL_CONFIG_DIR"]))


@pytest.fixture
def fake_file_manager():
    """org.freedesktop.FileManager1 on the private bus: the sandbox bus activates nothing real."""
    from fakebus import FakeFileManager
    fm = FakeFileManager()
    yield fm
    fm.stop()
