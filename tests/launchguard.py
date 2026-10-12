"""No unit test may open a browser, file manager or app on the human's desktop.

Every way ShareL (or GTK on its behalf) hands a URI or file to the desktop is replaced by a recorder:
Gio.AppInfo.launch_default_for_uri(_async), Gio.DesktopAppInfo launches, Gtk.UriLauncher /
Gtk.FileLauncher, Gtk.show_uri(_full), and subprocesses of xdg-open / gio open and friends. Nothing
is launched. A test that expects a launch asks for the ``launched`` fixture and asserts on the list.
Any other launch fails the test (raised at the call, and again at teardown in case GTK swallowed
the exception inside a signal handler).
"""
from __future__ import annotations

import os
import subprocess

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

try:  # the same class as the deprecated Gio.DesktopAppInfo alias
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix
    DesktopAppInfo = GioUnix.DesktopAppInfo
except (ImportError, ValueError):
    DesktopAppInfo = Gio.DesktopAppInfo

OPENERS = {"xdg-open", "gnome-open", "kde-open", "kde-open5", "exo-open", "sensible-browser", "x-www-browser",
           "www-browser", "firefox", "google-chrome", "chromium", "nautilus", "gio"}


class LaunchBlocked(AssertionError):
    pass


class Recorder(list):
    """(kind, target) per attempted launch."""

    def __init__(self) -> None:
        super().__init__()
        self.allowed = False

    def hit(self, kind: str, target) -> None:
        self.append((kind, target))
        if not self.allowed:
            raise LaunchBlocked(f"a unit test tried to launch on the desktop: {kind} {target!r} "
                                "(use the `launched` fixture if this is expected)")


def _opener(argv) -> bool:
    if isinstance(argv, (str, bytes, os.PathLike)):
        parts = str(argv if not isinstance(argv, bytes) else argv.decode()).split()
    else:
        parts = [str(a) for a in argv]
    if not parts:
        return False
    name = os.path.basename(parts[0])
    if name == "gio":
        return len(parts) > 1 and parts[1] in ("open", "launch")
    return name in OPENERS


def install(monkeypatch, rec: Recorder) -> None:
    def static(kind):
        def f(target, *_a, **_k):
            rec.hit(kind, target)
            return True
        return staticmethod(f)

    def method(kind, arg=0):
        def f(self, *a, **_k):
            rec.hit(kind, a[arg] if len(a) > arg else getattr(self, "get_uri", lambda: self)())
            return True
        return f

    monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri", static("launch_default_for_uri"))
    monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri_async", static("launch_default_for_uri_async"))
    for name in ("launch", "launch_uris", "launch_uris_async", "launch_uris_as_manager"):
        monkeypatch.setattr(DesktopAppInfo, name, method(f"DesktopAppInfo.{name}"))
    monkeypatch.setattr(Gtk.UriLauncher, "launch", method("UriLauncher.launch", arg=99))
    monkeypatch.setattr(Gtk.FileLauncher, "launch", method("FileLauncher.launch", arg=99))
    monkeypatch.setattr(Gtk.FileLauncher, "open_containing_folder", method("FileLauncher.open_containing_folder",
                                                                          arg=99))
    monkeypatch.setattr(Gtk, "show_uri", lambda _parent, uri, *_a: rec.hit("show_uri", uri))
    monkeypatch.setattr(Gtk, "show_uri_full", lambda _parent, uri, *_a: rec.hit("show_uri_full", uri))

    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *a, **k):
            if _opener(args):
                rec.hit("subprocess", args)
                args = ["true"]  # recorded, never run: keep a valid Popen for the caller
            super().__init__(args, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
