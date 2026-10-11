"""Shared plumbing for the tool windows: one window per tool, background workers with progress and
Cancel, small widget helpers.

* :class:`ToolWindow` — ``Adw.Window`` with a header bar, a toast overlay and a scrolling body. Subclasses
  call :meth:`ToolWindow.present_single` (via :func:`single`) so a tool opened twice re-presents its window;
  closing destroys it, cancels its running workers and forgets it.
* :class:`Worker` — run ``fn(progress, cancel)`` on a thread; ``on_done`` / ``on_error`` / ``on_progress`` are
  called on the GTK main loop. ``cancel()`` sets the event (the tool functions raise ``OperationCancelled``).
* :class:`ProgressPanel` — progress bar + status label + Cancel button bound to a worker.
"""
from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...tools._common import OperationCancelled
from ..gtkutil import alert

log = logging.getLogger("sharel.ui.tools")

# key -> live window (one per tool, or per tool + key such as an OCR window per image)
_open: dict[str, ToolWindow] = {}


def single(key: str, factory: Callable[[], ToolWindow]) -> ToolWindow:
    """Re-present the window registered under ``key``, or create it with ``factory()`` and present it."""
    win = _open.get(key)
    if win is not None:
        win.present()
        return win
    win = factory()
    win._single_key = key
    _open[key] = win
    win.present()
    return win


def existing(key: str) -> ToolWindow | None:
    return _open.get(key)


class Worker:
    """``fn(progress, cancel)`` on a daemon thread; callbacks on the main loop."""

    def __init__(self, fn: Callable[[Callable[[int, int], None], threading.Event], Any],
                 on_done: Callable[[Any], None] | None = None,
                 on_error: Callable[[BaseException], None] | None = None,
                 on_progress: Callable[[int, int], None] | None = None,
                 on_cancelled: Callable[[], None] | None = None,
                 on_finally: Callable[[], None] | None = None) -> None:
        self.fn = fn
        self.on_done = on_done
        self.on_error = on_error
        self.on_progress = on_progress
        self.on_cancelled = on_cancelled
        self.on_finally = on_finally
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.finished = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        self.cancelled = False
        self._last_progress = 0.0

    @property
    def running(self) -> bool:
        return self.thread is not None and not self.finished.is_set()

    def start(self) -> Worker:
        self.thread = threading.Thread(target=self._run, daemon=True, name="sharel-tool-worker")
        self.thread.start()
        return self

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, done: int, total: int) -> None:
        if self.on_progress is None:
            return
        GLib.idle_add(lambda: (self.on_progress(done, total), GLib.SOURCE_REMOVE)[1])

    def _run(self) -> None:
        try:
            self.result = self.fn(self._progress, self.cancel_event)
        except OperationCancelled:
            self.cancelled = True
        except BaseException as e:
            self.error = e
            log.debug("tool worker failed: %s", "".join(traceback.format_exception(e)))
        if self.cancel_event.is_set():
            self.cancelled = True
        GLib.idle_add(self._finish)

    def _finish(self) -> bool:
        try:
            if self.cancelled:
                if self.on_cancelled:
                    self.on_cancelled()
            elif self.error is not None:
                if self.on_error:
                    self.on_error(self.error)
                else:
                    log.error("tool worker failed: %s", self.error)
            elif self.on_done:
                self.on_done(self.result)
        finally:
            self.finished.set()
            if self.on_finally:
                self.on_finally()
        return GLib.SOURCE_REMOVE


class ProgressPanel(Gtk.Box):
    """Status label, progress bar and a Cancel button; ``attach(worker)`` drives it."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER, show_text=True, text="")
        self.status = Gtk.Label(xalign=0, ellipsize=3, width_chars=12)
        self.status.add_css_class("dim-label")
        self.cancel_button = Gtk.Button(label="Cancel", sensitive=False, tooltip_text="Stop the running operation")
        self.cancel_button.connect("clicked", lambda *_: self.worker and self.worker.cancel())
        self.append(self.status)
        self.append(self.bar)
        self.append(self.cancel_button)
        self.worker: Worker | None = None
        self.fraction = 0.0

    def attach(self, worker: Worker, status: str = "Working…") -> None:
        self.worker = worker
        self.cancel_button.set_sensitive(True)
        self.set_status(status)
        self.set_fraction(0.0)

    def detach(self, status: str = "") -> None:
        self.worker = None
        self.cancel_button.set_sensitive(False)
        self.set_status(status)

    def set_status(self, text: str) -> None:
        self.status.set_label(text)

    def set_fraction(self, f: float) -> None:
        self.fraction = max(0.0, min(1.0, f))
        self.bar.set_fraction(self.fraction)
        self.bar.set_text(f"{self.fraction * 100:.0f}%")

    def progress(self, done: int, total: int) -> None:
        if total > 0:
            self.set_fraction(done / total)
        else:
            self.bar.pulse()


class ToolWindow(Adw.Window):
    """Base window: header bar, toast overlay, body. Workers started with :meth:`run` die with it."""

    def __init__(self, app, title: str, width: int = 640, height: int = 520, *, scroll: bool = False) -> None:
        super().__init__(application=app, title=title, default_width=width, default_height=height)
        self.app = app
        self._single_key = ""
        self.workers: list[Worker] = []
        self.header = Adw.HeaderBar()
        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(self.header)
        self.toasts = Adw.ToastOverlay(child=self.toolbar)
        self.set_content(self.toasts)
        self._scroll = scroll
        self.connect("close-request", self._on_close_request)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    # -- layout --

    def set_body(self, widget: Gtk.Widget) -> None:
        if self._scroll:
            widget = Gtk.ScrolledWindow(child=widget, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.toolbar.set_content(widget)

    def add_bottom(self, widget: Gtk.Widget) -> None:
        self.toolbar.add_bottom_bar(widget)

    # -- feedback --

    def toast(self, text: str, timeout: int = 2) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def error(self, heading: str, body: str | BaseException = "") -> None:
        alert(self, heading, str(body))

    # -- workers --

    def run(self, fn, on_done=None, *, on_error=None, on_progress=None, on_cancelled=None, on_finally=None,
            panel: ProgressPanel | None = None, status: str = "Working…") -> Worker:
        """Start ``fn(progress, cancel)`` on a thread; errors default to an alert."""
        def finally_():
            if w in self.workers:
                self.workers.remove(w)
            if panel is not None and panel.worker is w:
                panel.detach(panel.status.get_label() if w.error is None and not w.cancelled else
                             ("Cancelled" if w.cancelled else "Failed"))
            if on_finally:
                on_finally()

        def progress(done, total):
            if panel is not None:
                panel.progress(done, total)
            if on_progress:
                on_progress(done, total)

        def done(res):
            if panel is not None:
                panel.set_fraction(1.0)
                panel.set_status("Done")
            if on_done:
                on_done(res)

        w = Worker(fn, done, on_error or (lambda e: self.error("Error", e)), progress, on_cancelled, finally_)
        if panel is not None:
            panel.attach(w, status)
        self.workers.append(w)
        return w.start()

    def busy(self) -> bool:
        return any(w.running for w in self.workers)

    # -- lifecycle --

    def _on_key(self, _c, keyval, _code, state) -> bool:
        if keyval == Gdk.KEY_Escape and self.close_on_escape():
            self.close()
            return True
        return False

    def close_on_escape(self) -> bool:
        return True

    def _on_close_request(self, *_a) -> bool:
        self._teardown()
        return False

    def _teardown(self) -> None:
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        try:
            self.on_closed()
        finally:
            for w in list(self.workers):
                w.cancel()
            if self._single_key and _open.get(self._single_key) is self:
                del _open[self._single_key]

    def on_closed(self) -> None:
        """Subclass hook: stop timers etc. Runs once, on close or destroy."""

    def destroy(self) -> None:
        self._teardown()
        super().destroy()


# ---- widget helpers ---------------------------------------------------------------------------


def labeled(label: str, widget: Gtk.Widget, width_chars: int = 0) -> Gtk.Box:
    box = Gtk.Box(spacing=6)
    lab = Gtk.Label(label=label, xalign=0)
    if width_chars:
        lab.set_width_chars(width_chars)
    lab.set_mnemonic_widget(widget)
    box.append(lab)
    box.append(widget)
    return box


def button(label: str, cb: Callable[[], Any], tooltip: str = "", icon: str = "", css: str = "") -> Gtk.Button:
    if icon:
        b = Gtk.Button(tooltip_text=tooltip or label)
        content = Adw.ButtonContent(icon_name=icon, label=label)
        b.set_child(content)
    else:
        b = Gtk.Button(label=label, tooltip_text=tooltip or None)
    if css:
        b.add_css_class(css)
    b.connect("clicked", lambda *_: cb())
    return b


def text_of(view: Gtk.TextView) -> str:
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def set_text(view: Gtk.TextView, text: str) -> None:
    view.get_buffer().set_text(text)


def text_view(editable: bool = True, monospace: bool = False, height: int = 120) -> tuple[Gtk.TextView,
                                                                                          Gtk.ScrolledWindow]:
    view = Gtk.TextView(editable=editable, monospace=monospace, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6,
                        bottom_margin=6, left_margin=6, right_margin=6)
    sw = Gtk.ScrolledWindow(child=view, min_content_height=height, vexpand=True)
    sw.add_css_class("card")
    return view, sw


def dropdown(options: list[str], selected: int = 0, tooltip: str = "") -> Gtk.DropDown:
    dd = Gtk.DropDown.new_from_strings(options)
    dd.set_selected(max(0, min(selected, len(options) - 1)) if options else 0)
    if tooltip:
        dd.set_tooltip_text(tooltip)
    return dd


def dropdown_value(dd: Gtk.DropDown) -> str:
    item = dd.get_selected_item()
    return item.get_string() if item is not None else ""


def select_value(dd: Gtk.DropDown, value: str) -> bool:
    model = dd.get_model()
    for i in range(model.get_n_items()):
        if model.get_item(i).get_string() == value:
            dd.set_selected(i)
            return True
    return False


def spin(value: float, lo: float, hi: float, step: float = 1, digits: int = 0, tooltip: str = "") -> Gtk.SpinButton:
    adj = Gtk.Adjustment(value=value, lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
    s = Gtk.SpinButton(adjustment=adj, digits=digits, numeric=True)
    if tooltip:
        s.set_tooltip_text(tooltip)
    return s


def file_drop_target(widget: Gtk.Widget, on_paths: Callable[[list[str]], None]) -> Gtk.DropTarget:
    """Accept files dropped on ``widget``."""
    from gi.repository import Gdk as _Gdk
    target = Gtk.DropTarget.new(_Gdk.FileList, _Gdk.DragAction.COPY)

    def dropped(_t, value, _x, _y):
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if paths:
            on_paths(paths)
            return True
        return False
    target.connect("drop", dropped)
    widget.add_controller(target)
    return target


def stored_tool_settings(app, ctx=None):
    """The ``TaskSettingsTools`` a tool should write its remembered options to, and the save function.

    ``ctx.task_settings`` is an effective *copy*, so edits to it are lost. Like ShareX
    (``TaskSettings.ToolsSettingsReference``) this is the workflow's own tool settings when the workflow
    does not use the defaults, otherwise the default task settings."""
    wf = getattr(ctx, "workflow", None) if ctx is not None else None
    wts = getattr(wf, "task_settings", None)
    if wts is not None and not wts.use_default_tool_settings:
        return wts.tool_settings, app.store.save_hotkeys
    return app.store.app.default_task_settings.tool_settings, app.store.save_app
