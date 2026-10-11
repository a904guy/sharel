"""The extension's pure logic under gjs, plus static checks of its D-Bus interface."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension" / "sharel@hawkins.app"


def test_logic_under_gjs():
    gjs = shutil.which("gjs")
    assert gjs, "gjs is required (apt install gjs)"
    out = subprocess.run([gjs, "-m", str(ROOT / "tests" / "js" / "test_logic.js")], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    m = re.search(r"(\d+)/(\d+) passed", out.stdout)
    assert m and m.group(1) == m.group(2) and int(m.group(2)) >= 30


def test_metadata():
    md = json.loads((EXT / "metadata.json").read_text())
    assert md["uuid"] == "sharel@hawkins.app"
    assert "50" in md["shell-version"]


def _interface_methods():
    xml = (EXT / "dbus.js").read_text()
    return set(re.findall(r'<method name="(\w+)"', xml)), set(re.findall(r'<signal name="(\w+)"', xml))


def test_every_dbus_method_is_implemented():
    methods, signals = _interface_methods()
    src = (EXT / "extension.js").read_text()
    for m in methods:
        assert re.search(rf"\n    (async )?{m}(Async)?\(", src), f"{m} not implemented in extension.js"
    assert signals == {"AcceleratorActivated", "TrayClicked"}


@pytest.mark.parametrize("name", ["GetMonitors", "GetWindows", "GetActiveWindow", "GetPointer", "SetAbove",
                                  "MoveResize", "MakeBorderless", "MakeFullscreen", "Screenshot", "PickWindow",
                                  "SetClipboard", "MouseHighlighter", "GrabAccelerator", "UngrabAccelerator",
                                  "PositionWindow"])
def test_plan_api_present(name):
    methods, _ = _interface_methods()
    assert name in methods


def test_python_client_matches_interface():
    """Every method the Python client calls exists in the interface."""
    methods, _ = _interface_methods()
    client = (ROOT / "src" / "sharel" / "shell" / "client.py").read_text()
    called = set(re.findall(r'self\._call\("(\w+)"', client))
    assert called, "client calls nothing?"
    assert called <= methods, called - methods
