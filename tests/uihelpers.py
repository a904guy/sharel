"""Run the real ShareL app inside the unit sandbox (private bus + headless weston) and drive it.

    def test_x():
        def body(app):
            win = open_something(app)
            yield 0.3                      # let GTK map / lay out (seconds)
            assert win.get_mapped()
            return "anything"
        assert run_app(body) == "anything"

``body(app)`` runs on the main loop once the app has started (silent, no tray, no hotkeys, no
extension). It may be a plain function or a generator yielding wait times. Exceptions propagate.
"""
from __future__ import annotations

import os
import types
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk


def run_app(body: Callable, timeout: float = 30.0, argv: list[str] | None = None):
    os.environ["SHAREL_NO_TRAY"] = "1"
    os.environ["SHAREL_DISABLE_HOTKEYS"] = "1"
    os.environ["SHAREL_NO_EXTENSION"] = "1"
    from sharel.app import ShareLApp
    from sharel.shell.client import ShellClient
    ShellClient._instance = None
    app = ShareLApp()
    box: dict = {}

    def finish():
        for w in list(app.get_windows()):
            w.destroy()
        app.quit_app()

    def step(gen):
        try:
            delay = next(gen)
        except StopIteration as e:
            box["result"] = e.value
            finish()
            return False
        except BaseException as e:
            box["error"] = e
            finish()
            return False
        GLib.timeout_add(int(max(0.0, float(delay or 0)) * 1000), step, gen)
        return False

    def start():
        try:
            res = body(app)
        except BaseException as e:
            box["error"] = e
            finish()
            return False
        if isinstance(res, types.GeneratorType):
            GLib.idle_add(step, res)
        else:
            box["result"] = res
            finish()
        return False

    def on_timeout():
        box.setdefault("error", TimeoutError(f"UI test did not finish within {timeout}s"))
        finish()
        return False

    app.connect("startup", lambda *_: GLib.idle_add(start))
    GLib.timeout_add(int(timeout * 1000), on_timeout)
    app.run(["sharel", *(argv or ["-silent"])])
    if "error" in box:
        raise box["error"]
    return box.get("result")


def find(widget: Gtk.Widget, pred: Callable[[Gtk.Widget], bool]) -> Gtk.Widget | None:
    """Depth-first search of a widget tree."""
    if pred(widget):
        return widget
    child = widget.get_first_child()
    while child is not None:
        hit = find(child, pred)
        if hit is not None:
            return hit
        child = child.get_next_sibling()
    return None


def find_all(widget: Gtk.Widget, pred: Callable[[Gtk.Widget], bool]) -> list[Gtk.Widget]:
    out = []
    if pred(widget):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        out += find_all(child, pred)
        child = child.get_next_sibling()
    return out


def labels(widget: Gtk.Widget) -> list[str]:
    out = []
    for w in find_all(widget, lambda w: isinstance(w, (Gtk.Label, Gtk.Button))):
        if isinstance(w, Gtk.Label):
            out.append(w.get_label() or "")
    return out


def button_with_label(widget: Gtk.Widget, text: str) -> Gtk.Widget | None:
    def match(w):
        if isinstance(w, Gtk.Button) and (w.get_label() or "") == text:
            return True
        if isinstance(w, Gtk.Button):
            lab = find(w, lambda c: isinstance(c, Gtk.Label) and c.get_label() == text)
            return lab is not None
        return False
    return find(widget, match)
