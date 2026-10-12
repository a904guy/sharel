"""Shared plumbing for the tool windows: one window per tool, background workers with progress and
Cancel, small widget helpers.

* :class:`ToolWindow` — ``Adw.Window`` with a header bar, a toast overlay and a scrolling body. Subclasses
  call :meth:`ToolWindow.present_single` (via :func:`single`) so a tool opened twice re-presents its window;
  closing destroys it, cancels its running workers and forgets it.
* :class:`Worker` — run ``fn(progress, cancel)`` on a thread; ``on_done`` / ``on_error`` / ``on_progress`` are
  called on the GTK main loop. ``cancel()`` sets the event (the tool functions raise ``OperationCancelled``).
* :class:`ProgressPanel` — progress bar + status label + Cancel button bound to a worker.
* Image tools: :class:`FileList` (inputs with add/remove/reorder and drag and drop), :class:`OutputFolder`,
  :class:`BatchBar` (progress + Cancel for batches), :class:`LivePreview` (debounced background rendering with
  stale-result protection), :class:`ImagePane`, :class:`ToolSettingsWriter`, ``load_image_file``,
  ``clipboard_image``, ``output_path``, ``upload_image``.
"""
from __future__ import annotations

import logging
import os
import threading
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from PIL import Image

from ...core import clipboard, desktop
from ...tools._common import OperationCancelled
from .. import gtkutil
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
        self.toolbar_view = self.toolbar
        self.toasts = Adw.ToastOverlay(child=self.toolbar)
        self.set_content(self.toasts)
        self.last_message = ""
        self._scroll = scroll
        self.connect("close-request", self._on_close_request)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    # -- one window per class --

    @classmethod
    def present_single(cls, app, *args, **kw) -> ToolWindow:
        """Re-present this class's open window (for the same app), or create and present one."""
        key = f"class:{cls.__module__}.{cls.__qualname__}"
        win = _open.get(key)
        if win is not None and (win.app is not app or win.get_application() is not app):
            _open.pop(key, None)  # left over from an application that has quit
        return single(key, lambda: cls(app, *args, **kw))

    @classmethod
    def instance(cls) -> ToolWindow | None:
        return _open.get(f"class:{cls.__module__}.{cls.__qualname__}")

    # -- layout --

    def set_body(self, widget: Gtk.Widget) -> None:
        if self._scroll:
            widget = Gtk.ScrolledWindow(child=widget, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.toolbar.set_content(widget)

    def add_bottom(self, widget: Gtk.Widget) -> None:
        self.toolbar.add_bottom_bar(widget)

    # -- feedback --

    def toast(self, text: str, timeout: int = 3) -> None:
        self.last_message = text
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def error(self, heading: str, body: str | BaseException = "") -> None:
        body = str(body)
        self.last_message = f"{heading}: {body}" if body else heading
        log.warning("%s: %s", heading, body)
        alert(self, heading, body)

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


def button(label: str, cb: Callable[[], Any], tooltip: str = "", icon: str = "", css: str = "", *,
           suggested: bool = False) -> Gtk.Button:
    """A button whose accessible label is ``label``. With an icon it shows icon + label (icon only when
    ``label`` is empty); ``suggested`` / ``css`` add style classes."""
    if icon and label:
        b = Gtk.Button(tooltip_text=tooltip or label, child=Adw.ButtonContent(icon_name=icon, label=label))
    elif icon:
        b = Gtk.Button(icon_name=icon, tooltip_text=tooltip or None)
    else:
        b = Gtk.Button(label=label, tooltip_text=tooltip or None)
    if label:
        b.update_property([Gtk.AccessibleProperty.LABEL], [label])
    b._label = label  # type: ignore[attr-defined]
    if suggested:
        b.add_css_class("suggested-action")
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
    gtkutil.text_view_no_hyphens(view)  # OCR and QR text, clipboard contents: often URLs and paths
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


# ---- image tools ------------------------------------------------------------------------

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico")


def is_image_path(path: str) -> bool:
    return str(path).lower().endswith(IMAGE_EXTS)


class WindowList:
    """Windows of one kind that may be open several times. A destroyed GTK 4 window leaves its application
    (its "destroy" signal only comes when the last reference goes), so membership is checked that way."""

    def __init__(self) -> None:
        self._items: list[Gtk.Window] = []

    def add(self, win: Gtk.Window) -> Gtk.Window:
        self._items = [*self.live(), win]
        return win

    def live(self) -> list[Gtk.Window]:
        self._items = [w for w in self._items if w.get_application() is not None]
        return list(self._items)


def scrolled(child: Gtk.Widget, min_h: int = 0, min_w: int = 0) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow(child=child, vexpand=True, hexpand=True)
    if min_h:
        sw.set_min_content_height(min_h)
    if min_w:
        sw.set_min_content_width(min_w)
    return sw


def prefs_box(*groups: Gtk.Widget) -> Gtk.ScrolledWindow:
    """Preference groups stacked in a scrolled column."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                  margin_start=12, margin_end=12)
    for g in groups:
        box.append(g)
    return scrolled(box)


def spin_row(title: str, value: float, lo: float, hi: float, changed: Callable[[float], None],
             step: float = 1, digits: int = 0, subtitle: str = "") -> Adw.SpinRow:
    adj = Gtk.Adjustment(value=value, lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
    row = gtkutil.spin_row_widget(title=title, subtitle=subtitle, adjustment=adj, digits=digits)
    row.connect("notify::value", lambda r, _p: changed(r.get_value()))
    return row


def combo_row(title: str, options: Sequence[str], selected: int, changed: Callable[[int], None]) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(options)),
                       selected=max(0, min(selected, len(options) - 1)))
    row.connect("notify::selected", lambda r, _p: changed(r.get_selected()))
    return row


def switch_row(title: str, active: bool, changed: Callable[[bool], None], subtitle: str = "") -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=active)
    row.connect("notify::active", lambda r, _p: changed(r.get_active()))
    return row


def entry_row(title: str, text: str, changed: Callable[[str], None]) -> Adw.EntryRow:
    row = Adw.EntryRow(title=title, text=text)
    row.connect("changed", lambda r: changed(r.get_text()))
    return row


def color_row(title: str, value: str, changed: Callable[[str], None]) -> Adw.ActionRow:
    from ..forms import hex_from_rgba, rgba_from_hex
    row = Adw.ActionRow(title=title)
    btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True), valign=Gtk.Align.CENTER)
    btn.set_rgba(rgba_from_hex(value))
    btn.connect("notify::rgba", lambda b, _p: changed(hex_from_rgba(b.get_rgba())))
    row.add_suffix(btn)
    row.set_activatable_widget(btn)
    row.color_button = btn  # type: ignore[attr-defined]
    return row


# ---- inputs ---------------------------------------------------------------------------------

class FileList(Gtk.Box):
    """A list of input files with Add/Remove/Clear (and Move up/down) plus drag and drop."""

    def __init__(self, title: str = "Images", *, patterns: list[str] | None = None, filter_name: str = "Images",
                 on_change: Callable[[], None] | None = None, reorder: bool = False,
                 accept: Callable[[str], bool] | None = is_image_path) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.paths: list[str] = []
        self.on_change = on_change or (lambda: None)
        self.on_select: Callable[[], None] = lambda: None
        self.patterns = gtkutil.IMAGE_PATTERNS if patterns is None else patterns
        self.filter_name = filter_name
        self.accept = accept
        self.title_label = Gtk.Label(label=title, xalign=0)
        self.title_label.add_css_class("heading")
        self.append(self.title_label)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("selected-rows-changed", lambda *_: (self._sync_buttons(), self.on_select()))
        self.placeholder = Gtk.Label(label="Add files or drop them here", margin_top=24, margin_bottom=24)
        self.placeholder.add_css_class("dim-label")
        self.listbox.set_placeholder(self.placeholder)
        self.append(scrolled(self.listbox, min_h=160))
        bar = Gtk.Box(spacing=6)
        self.add_button = button("Add…", self.browse, tooltip="Add files")
        self.remove_button = button("Remove", self.remove_selected)
        self.clear_button = button("Clear", self.clear)
        for b in (self.add_button, self.remove_button, self.clear_button):
            bar.append(b)
        self.up_button = self.down_button = None
        if reorder:
            self.up_button = button("Move up", lambda: self.move_selected(-1), icon="go-up-symbolic")
            self.down_button = button("Move down", lambda: self.move_selected(1), icon="go-down-symbolic")
            bar.append(self.up_button)
            bar.append(self.down_button)
        self.count_label = Gtk.Label(label="", hexpand=True, xalign=1)
        self.count_label.add_css_class("dim-label")
        bar.append(self.count_label)
        self.append(bar)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, value, _x, _y: self.handle_drop(value))
        self.add_controller(drop)
        self._refresh()

    # -- API --

    def handle_drop(self, value) -> bool:
        files = value.get_files() if hasattr(value, "get_files") else list(value)
        paths = [f.get_path() if isinstance(f, Gio.File) else str(f) for f in files]
        return bool(self.add_paths([p for p in paths if p]))

    def add_paths(self, paths: Sequence[str]) -> int:
        added = 0
        for p in paths:
            p = os.path.abspath(str(p))
            if p in self.paths or not os.path.isfile(p):
                continue
            if self.accept is not None and not self.accept(p):
                continue
            self.paths.append(p)
            added += 1
        if added:
            self._refresh()
            self.on_change()
        return added

    def set_paths(self, paths: Sequence[str]) -> None:
        self.paths = []
        self._refresh()
        self.add_paths(paths)
        if not paths:
            self.on_change()

    def browse(self) -> None:
        root = self.get_root()
        gtkutil.open_files(root if isinstance(root, Gtk.Window) else None, "Add files", self.add_paths,
                           patterns=self.patterns or None, filter_name=self.filter_name)

    def selected_indices(self) -> list[int]:
        return sorted(r.get_index() for r in self.listbox.get_selected_rows())

    def selected_path(self) -> str:
        idx = self.selected_indices()
        if idx:
            return self.paths[idx[0]]
        return self.paths[0] if self.paths else ""

    def select(self, index: int) -> None:
        row = self.listbox.get_row_at_index(index)
        if row is not None:
            self.listbox.unselect_all()
            self.listbox.select_row(row)

    def remove_selected(self) -> None:
        idx = self.selected_indices()
        if not idx:
            return
        for i in reversed(idx):
            del self.paths[i]
        self._refresh()
        self.on_change()

    def clear(self) -> None:
        if self.paths:
            self.paths = []
            self._refresh()
            self.on_change()

    def move_selected(self, delta: int) -> None:
        idx = self.selected_indices()
        if len(idx) != 1:
            return
        i = idx[0]
        j = i + delta
        if not 0 <= j < len(self.paths):
            return
        self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
        self._refresh()
        self.select(j)
        self.on_change()

    # -- internals --

    def _refresh(self) -> None:
        self.listbox.remove_all()
        for p in self.paths:
            lbl = Gtk.Label(label=os.path.basename(p), xalign=0, tooltip_text=p, margin_top=6, margin_bottom=6,
                            margin_start=8, margin_end=8, ellipsize=Pango.EllipsizeMode.MIDDLE)
            self.listbox.append(lbl)
        n = len(self.paths)
        self.count_label.set_label("" if not n else ("1 file" if n == 1 else f"{n} files"))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        sel = self.selected_indices()
        self.remove_button.set_sensitive(bool(sel))
        self.clear_button.set_sensitive(bool(self.paths))
        if self.up_button is not None:
            self.up_button.set_sensitive(len(sel) == 1 and sel[0] > 0)
            self.down_button.set_sensitive(len(sel) == 1 and sel[0] < len(self.paths) - 1)


class OutputFolder(Adw.EntryRow):
    """Output folder: "" means next to each source file."""

    def __init__(self, folder: str = "", title: str = "Output folder (empty = same folder as the source)") -> None:
        super().__init__(title=title, text=folder)
        browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse…")
        browse.add_css_class("flat")
        browse.connect("clicked", lambda *_: self.browse())
        self.open_button = Gtk.Button(icon_name="folder-symbolic", valign=Gtk.Align.CENTER,
                                      tooltip_text="Open output folder")
        self.open_button.update_property([Gtk.AccessibleProperty.LABEL], ["Open output folder"])
        self.open_button.add_css_class("flat")
        self.open_button.connect("clicked", lambda *_: self.open_folder())
        self.add_suffix(browse)
        self.add_suffix(self.open_button)
        self.last_output = ""  # folder of the last written file, opened when no folder is set

    @property
    def path(self) -> str:
        return self.get_text().strip()

    def browse(self) -> None:
        root = self.get_root()
        gtkutil.select_folder(root if isinstance(root, Gtk.Window) else None, "Output folder",
                              lambda p: p and self.set_text(p))

    def open_folder(self) -> None:
        folder = self.path or self.last_output
        if folder and os.path.isdir(folder):
            desktop.open_folder(folder)


def output_path(src: str, folder: str, pattern: str, ext: str) -> Path:
    """``<folder or src folder>/<pattern with $filename = src stem>.<ext>``; never the source itself."""
    s = Path(src)
    d = Path(folder) if folder else s.parent
    name = (pattern or "$filename").replace("$filename", s.stem).strip() or s.stem
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    out = d / f"{name}.{ext.lstrip('.')}"
    if out.resolve() == s.resolve():
        out = d / f"{name}-1.{ext.lstrip('.')}"
    return out


# ---- work ------------------------------------------------------------------------------------

class BatchBar(Gtk.Box):
    """Progress + Cancel for work done on a thread.

    ``run(items, fn, done)`` calls ``fn(item, cancel)`` for each item off the main loop; ``done(results,
    errors, cancelled)`` runs on the main loop with ``results`` = list of return values and ``errors`` =
    ``[(item, message)]``. ``finished`` is set when the batch is over (tests poll it)."""

    def __init__(self) -> None:
        super().__init__(spacing=8)
        self.progress = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER, show_text=True, text="")
        self.cancel_button = button("Cancel", self.cancel)
        self.cancel_button.set_sensitive(False)
        self.append(self.progress)
        self.append(self.cancel_button)
        self._cancel = threading.Event()
        self.finished = threading.Event()
        self.finished.set()
        self.busy = False
        self.on_busy: Callable[[bool], None] = lambda _b: None

    def cancel(self) -> None:
        self._cancel.set()
        self.progress.set_text("Cancelling…")

    def run(self, items: Sequence[Any], fn: Callable[[Any, threading.Event], Any],
            done: Callable[[list, list, bool], None], label: Callable[[Any], str] | None = None) -> bool:
        if self.busy:
            return False
        items = list(items)
        self.busy = True
        self.finished.clear()
        self._cancel = threading.Event()
        cancel = self._cancel
        self.cancel_button.set_sensitive(True)
        self.progress.set_fraction(0)
        self.progress.set_text(f"0 / {len(items)}")
        self.on_busy(True)

        def tick(i: int, text: str) -> None:
            n = max(1, len(items))
            self.progress.set_fraction(i / n)
            self.progress.set_text(text)

        def work() -> None:
            results, errors, cancelled = [], [], False
            for i, item in enumerate(items):
                if cancel.is_set():
                    cancelled = True
                    break
                name = label(item) if label else (os.path.basename(str(item)) if item is not None else "")
                gtkutil.idle(tick, i, f"{i + 1} / {len(items)}  {name}".strip())
                try:
                    results.append(fn(item, cancel))
                except OperationCancelled:
                    cancelled = True
                    break
                except Exception as e:
                    log.warning("batch item %s failed: %s", item, e)
                    errors.append((item, str(e) or type(e).__name__))
            gtkutil.idle(finish, results, errors, cancelled or cancel.is_set())

        def finish(results, errors, cancelled) -> None:
            self.busy = False
            self.cancel_button.set_sensitive(False)
            ok = len(results)
            self.progress.set_fraction(1.0 if not cancelled else self.progress.get_fraction())
            self.progress.set_text("Cancelled" if cancelled else f"Done: {ok} ok" + (f", {len(errors)} failed"
                                                                                      if errors else ""))
            self.on_busy(False)
            try:
                done(results, errors, cancelled)
            finally:
                self.finished.set()

        threading.Thread(target=work, daemon=True, name="sharel-tool-batch").start()
        return True


class LivePreview:
    """Debounced rendering: ``schedule()`` (main loop) waits ``delay_ms``, takes ``snapshot()`` on the main
    loop, runs ``render(snapshot)`` on a thread and hands the newest result to ``deliver(img)`` (or
    ``fail(message)``). Older results that finish late are dropped. ``idle`` is set when nothing is pending."""

    def __init__(self, snapshot: Callable[[], Any], render: Callable[[Any], Any], deliver: Callable[[Any], None],
                 fail: Callable[[str], None], delay_ms: int = 150) -> None:
        self.snapshot = snapshot
        self.render = render
        self.deliver = deliver
        self.fail = fail
        self.delay_ms = delay_ms
        self.seq = 0
        self._timer = 0
        self.idle = threading.Event()
        self.idle.set()

    def schedule(self) -> None:
        self.seq += 1
        self.idle.clear()
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(self.delay_ms, self._fire)

    def cancel(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.seq += 1
        self.idle.set()

    def _fire(self) -> bool:
        self._timer = 0
        seq = self.seq
        try:
            snap = self.snapshot()
        except Exception as e:
            self.fail(str(e))
            self.idle.set()
            return False

        def work():
            try:
                res, err = self.render(snap), None
            except Exception as e:
                log.debug("preview failed", exc_info=True)
                res, err = None, str(e) or type(e).__name__
            gtkutil.idle(self._done, seq, res, err)
        threading.Thread(target=work, daemon=True, name="sharel-preview").start()
        return False

    def _done(self, seq: int, res, err) -> None:
        if seq != self.seq:
            return
        if err is not None:
            self.fail(err)
        else:
            self.deliver(res)
        self.idle.set()


# ---- images ---------------------------------------------------------------------------------

def load_image_file(path: str) -> Image.Image:
    from ...core import imaging
    return imaging.load(path)


def clipboard_image() -> tuple[Image.Image, str] | None:
    """The clipboard image (or the first image file on the clipboard) and its path ("" for bitmaps)."""
    c = clipboard.read()
    if c.kind == "image" and c.image is not None:
        return c.image, ""
    if c.kind == "files" and c.files:
        for f in c.files:
            if is_image_path(f) and os.path.isfile(f):
                return load_image_file(f), f
    return None


class ImagePane(Gtk.Box):
    """A picture (scaled to fit) with a caption; ``set_image(None)`` shows the placeholder text."""

    def __init__(self, placeholder: str = "No image", min_w: int = 320, min_h: int = 240) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, vexpand=True)
        self.image: Image.Image | None = None
        self.picture = gtkutil.picture(None)
        self.picture.set_size_request(min_w, min_h)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        ph = Gtk.Label(label=placeholder)
        ph.add_css_class("dim-label")
        self.stack.add_named(ph, "empty")
        frame = Gtk.Frame(child=self.picture)
        self.stack.add_named(frame, "image")
        self.caption = Gtk.Label(label="", xalign=0.5)
        self.caption.add_css_class("dim-label")
        self.append(self.stack)
        self.append(self.caption)

    def set_image(self, img: Image.Image | None, caption: str | None = None) -> None:
        self.image = img
        if img is None:
            self.picture.set_paintable(None)
            self.stack.set_visible_child_name("empty")
            self.caption.set_label(caption or "")
            return
        shown = img
        if max(img.size) > 2048:  # keep textures small; the full image is kept in self.image
            shown = img.copy()
            shown.thumbnail((2048, 2048))
        self.picture.set_paintable(gtkutil.texture_from_pil(shown))
        self.stack.set_visible_child_name("image")
        self.caption.set_label(caption if caption is not None else f"{img.width} × {img.height}")


def image_drop_target(on_paths: Callable[[list[str]], None]) -> Gtk.DropTarget:
    drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

    def dropped(_t, value, _x, _y):
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if paths:
            on_paths(paths)
            return True
        return False
    drop.connect("drop", dropped)
    return drop


class ToolSettingsWriter:
    """Writes tool options back where they live: the workflow's own tool settings when it overrides them,
    else the default task settings; the effective copy in ``ctx.task_settings`` is kept in step."""

    def __init__(self, app, ctx) -> None:
        wf = getattr(ctx, "workflow", None)
        own = wf is not None and not wf.task_settings.use_default_tool_settings
        stored = wf.task_settings.tool_settings if own else app.store.app.default_task_settings.tool_settings
        targets = [stored]
        if ctx.task_settings.tool_settings is not stored:
            targets.append(ctx.task_settings.tool_settings)
        object.__setattr__(self, "_targets", targets)
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "_hotkeys", own)

    def __getattr__(self, name):
        return getattr(self._targets[0], name)

    def __setattr__(self, name, value) -> None:
        for t in self._targets:
            setattr(t, name, value)

    def save(self) -> None:
        if self._hotkeys:
            self._app.store.save_hotkeys()
        else:
            self._app.store.save_app()
        self._app.settings_changed()


def save_tool_settings(app) -> None:
    app.store.save_app()
    app.settings_changed()


def upload_image(app, img: Image.Image, name: str, ts) -> Any:
    """Upload an image to the image destination (ShareX ``UploadManager.UploadImage``)."""
    from ...core import imaging
    data = imaging.png_bytes(img)
    stem = Path(name).stem if name else "image"
    return app.tasks.upload_data(data, f"{stem}.png", ts)
