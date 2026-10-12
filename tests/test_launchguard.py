"""The unit suite can never open a browser or app on the human's desktop (tests/launchguard.py)."""
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


INNER_CONFTEST = f"""
import importlib.util, sys
sys.path.insert(0, {str(TESTS)!r})
# the suite's conftest, loaded under another name ("from conftest import *" would import this file)
spec = importlib.util.spec_from_file_location("sharel_suite_conftest", {str(TESTS / "conftest.py")!r})
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)
globals().update({{k: v for k, v in vars(suite).items() if not k.startswith("__") and k != "pytest_plugins"}})
# fail closed before any test body: the sandbox and the launch guard must both be in place
import os
assert os.environ.get("SHAREL_UNIT_SANDBOX") == "1" and "sharel-unit-" in os.environ["DBUS_SESSION_BUS_ADDRESS"]
assert "no_desktop_launches" in globals(), "the launch guard fixture did not load"
"""


def _inner(pytester, body: str):
    pytester.makeconftest(INNER_CONFTEST)
    pytester.makepyfile(test_inner=body)
    return pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")


def test_unpatched_open_url_fails_the_test(pytester):
    r = _inner(pytester, """
def test_opens_a_browser():
    from sharel.core import desktop
    desktop.open_url("https://example.com/must-not-open")
""")
    assert r.ret != 0 and r.parseoutcomes().get("failed") == 1  # raised at the call
    out = r.stdout.str()
    assert "LaunchBlocked" in out and "launch_default_for_uri" in out and "must-not-open" in out, out


def test_a_launch_swallowed_by_a_callback_still_fails_the_test(pytester):
    # GTK signal handlers print exceptions and carry on: the teardown check catches it anyway
    r = _inner(pytester, """
def test_in_a_callback():
    from sharel.core import desktop
    try:
        desktop.show_in_folder("/tmp")  # no FileManager1 on the test bus -> opens the folder
    except Exception:
        pass
""")
    # the body "passed", but the run fails: the teardown check reports it as an error
    assert r.ret != 0 and r.parseoutcomes().get("errors") == 1
    assert "without the `launched` fixture" in r.stdout.str()


def test_a_broken_inner_conftest_fails_closed(pytester):
    """An inner run whose conftest doesn't load the suite's must error, not run (and launch)."""
    pytester.makeconftest(INNER_CONFTEST.replace("globals().update", "dict().update"))
    pytester.makepyfile(test_inner="def test_never_runs():\n    raise SystemExit('the body ran')\n")
    r = pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")
    assert r.ret != 0
    out = r.stdout.str() + r.stderr.str()
    assert "the launch guard fixture did not load" in out and "the body ran" not in out, out


def test_launched_fixture_records_instead_of_opening(launched, tmp_path):
    from sharel.core import desktop
    desktop.open_url("https://example.com/recorded")
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    desktop.open_file(str(f))
    p = subprocess.Popen(["xdg-open", "https://example.com/via-subprocess"])
    assert p.args == ["true"] and p.wait(5) == 0  # the harmless stand-in ran, not xdg-open
    assert launched == [("launch_default_for_uri", "https://example.com/recorded"),
                        ("launch_default_for_uri", f.resolve().as_uri()),
                        ("subprocess", ["xdg-open", "https://example.com/via-subprocess"])]


@pytest.mark.parametrize("argv,blocked", [(["xdg-open", "x"], True), (["gio", "open", "x"], True),
                                          (["gio", "info", "x"], False), (["true"], False),
                                          ("xdg-open https://x", True), (["/usr/bin/firefox", "x"], True)])
def test_opener_detection(argv, blocked):
    import launchguard
    assert launchguard._opener(argv) is blocked


def test_gtk_launchers_are_guarded(launched):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk
    Gtk.UriLauncher.new("https://example.com/gtk").launch(None, None, None)
    Gtk.FileLauncher.new(Gio.File.new_for_path("/tmp")).launch(None, None, None)
    Gtk.show_uri(None, "https://example.com/show", 0)
    assert [k for k, _ in launched] == ["UriLauncher.launch", "FileLauncher.launch", "show_uri"]
