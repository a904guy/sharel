"""Placing ShareL's own windows on Wayland, through the extension.

A Wayland client can't position, raise or keep its windows on top, and GNOME's focus-stealing
prevention won't focus a window opened from a global hotkey. The extension can: find our window
by pid + a unique title, then Activate / SetAbove / PositionWindow it.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from ..shell.client import ShellUnavailable, Window, shell

log = logging.getLogger("sharel.placement")


def unique_title(base: str) -> str:
    """A title the extension can find; the zero-width tag keeps it looking like ``base``."""
    return f"{base}​{uuid.uuid4().hex[:8]}"


def find_own(title: str) -> Window | None:
    sh = shell()
    if not sh.available:
        return None
    try:
        wins = sh.find_windows(os.getpid(), title)
    except ShellUnavailable:
        return None
    return wins[0] if wins else None


def when_mapped(win: Gtk.Window, fn: Callable[[Window], None], timeout_ms: int = 3000) -> None:
    """Call ``fn(shell_window)`` once the shell knows our window (polls briefly after map)."""
    title = win.get_title() or ""
    tries = {"n": 0}

    def poll() -> bool:
        tries["n"] += 1
        w = find_own(title)
        if w is not None:
            try:
                fn(w)
            except ShellUnavailable as e:
                log.info("placement failed: %s", e)
            return False
        return tries["n"] * 50 < timeout_ms

    def on_map(*_a):
        GLib.timeout_add(50, poll)
    if win.get_mapped():
        on_map()
    else:
        win.connect("map", on_map)


def raise_and_focus(win: Gtk.Window, above: bool = False) -> None:
    def go(w: Window) -> None:
        sh = shell()
        sh.activate(w.id)
        if above:
            sh.set_above(w.id, True)
    when_mapped(win, go)


def keep_above(win: Gtk.Window, position: tuple[int, int] | None = None, activate: bool = False) -> None:
    def go(w: Window) -> None:
        sh = shell()
        if position is not None:
            sh.position_window(w.id, *position)
        sh.set_above(w.id, True)
        if activate:
            sh.activate(w.id)
    when_mapped(win, go)


def position(win: Gtk.Window, x: int, y: int) -> None:
    when_mapped(win, lambda w: shell().position_window(w.id, x, y))
