from sharel.core.config import HotkeyInfo, HotkeySettings, TaskSettings
from sharel.core.enums import HotkeyType
from sharel.shell.hotkeys import _portal_trigger, command_for, find_conflicts, label, normalize


def hk(accel, job="RectangleRegion", desc="", enabled=True):
    return HotkeySettings(HotkeyInfo(accel, enabled), TaskSettings(description=desc, job=HotkeyType[job]))


def test_normalize_and_label():
    assert normalize("<Ctrl><shift>print") == normalize("<Shift><Control>Print") == "<Shift><Control>Print"
    assert label("<Control>Print") in ("Ctrl+Print", "Ctrl+Print Screen")


def test_conflicts_duplicates_and_gnome():
    keys = [hk("<Control>Print"), hk("<Ctrl>Print", "PrintScreen"), hk("<Super>a", "OCR"),
            hk("<Control>Print", "Ruler", enabled=False), hk("", "ColorPicker")]
    gnome = {normalize("<Super>a"): "org.gnome.shell.keybindings toggle-application-view"}
    c = find_conflicts(keys, gnome)
    assert set(c) == {0, 1, 2}
    assert "also used by" in c[0] and "Capture entire screen" in c[0]
    assert c[2].startswith("GNOME uses it")


def test_gsettings_command_lines():
    assert command_for(hk("<Super>p", "PrintScreen")).endswith(" -PrintScreen")
    assert command_for(hk("<Super>p", "PrintScreen", desc="My flow")).endswith(" -workflow 'My flow'")


def test_portal_trigger_syntax():
    assert _portal_trigger("<Shift><Control>Print") == "SHIFT+CTRL+Print"
    assert _portal_trigger("<Super>a") == "LOGO+a"
