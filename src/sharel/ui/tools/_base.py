"""Shared building blocks for the image tool windows.

* :class:`ToolWindow` - an ``Adw.Window`` with a header bar and a toast overlay; ``present_single`` keeps one
  window per class (a tool opened twice re-presents its window; closing destroys and forgets it).
* :class:`FileList` - the input list: Add (file chooser), Remove, Clear, optional Move up/down, drag and
  drop of files from the file manager, and ``add_paths`` for code (CLI files, tests).
* :class:`OutputFolder` - output folder entry with Browse and "Open output folder".
* :class:`BatchBar` - progress bar + Cancel; runs work on a thread, reports back on the main loop.
* :class:`LivePreview` - debounced background rendering with stale-result protection.
* image helpers: ``load_image_file``, ``clipboard_image``, ``output_path`` (``$filename`` patterns).
"""
from __future__ import annotations

import logging
import os
import threading
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

log = logging.getLogger("sharel.ui.tools")

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico")


def is_image_path(path: str) -> bool:
    return str(path).lower().endswith(IMAGE_EXTS)


# ---- windows --------------------------------------------------------------------------------

class ToolWindow(Adw.Window):
    """Base window: header bar on top, ``self.toasts`` overlay holding the body set by ``set_body``."""

    _instances: dict[type, ToolWindow] = {}

    def __init__(self, app, title: str, width: int = 900, height: int = 640) -> None:
        super().__init__(application=app, title=title, default_width=width, default_height=height)
        self.app = app
        self.header = Adw.HeaderBar()
        self.toasts = Adw.ToastOverlay()
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.header)
        self.toolbar_view.set_content(self.toasts)
        self.set_content(self.toolbar_view)
        self.last_message = ""
        self.connect("destroy", self._forget)
        self.connect("close-request", lambda *_a: (self._forget(), False)[1])

    def _forget(self, *_a) -> None:
        if ToolWindow._instances.get(type(self)) is self:
            del ToolWindow._instances[type(self)]

    @classmethod
    def present_single(cls, app, *args, **kw) -> ToolWindow:
        win = ToolWindow._instances.get(cls)
        if win is not None and (win.app is not app or win.get_application() is not app):
            win = None  # left over from an application that has quit
        if win is None:
            win = cls(app, *args, **kw)
            ToolWindow._instances[cls] = win
        win.present()
        return win

    @classmethod
    def instance(cls) -> ToolWindow | None:
        return ToolWindow._instances.get(cls)

    def set_body(self, widget: Gtk.Widget) -> None:
        self.toasts.set_child(widget)

    def toast(self, text: str) -> None:
        self.last_message = text
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def error(self, heading: str, body: str = "") -> None:
        self.last_message = f"{heading}: {body}" if body else heading
        log.warning("%s: %s", heading, body)
        gtkutil.alert(self, heading, body)


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


def button(label: str, cb: Callable[[], Any], *, icon: str = "", tooltip: str = "",
           suggested: bool = False) -> Gtk.Button:
    """A button whose accessible label is ``label`` (icon buttons get it as tooltip)."""
    if icon:
        b = Gtk.Button(icon_name=icon, tooltip_text=tooltip or label)
        b.update_property([Gtk.AccessibleProperty.LABEL], [label])
        b._label = label  # type: ignore[attr-defined]
    else:
        b = Gtk.Button(label=label)
        if tooltip:
            b.set_tooltip_text(tooltip)
    if suggested:
        b.add_css_class("suggested-action")
    b.connect("clicked", lambda *_: cb())
    return b


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
    row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adj, digits=digits)
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
