"""Region capture (ShareX's RegionCaptureForm) on GNOME Wayland.

The whole desktop is captured first (frozen), then one undecorated fullscreen Gtk.Window per
monitor shows its slice. All windows share one ShapeManager, so a selection or annotation can
cross monitors. Window/child-area snapping uses the rects from the extension's GetWindows (frame,
client area). The same session runs every RegionCaptureMode: Default, Annotation,
ScreenColorPicker, Ruler, OneClick, Editor, TaskEditor.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk
from PIL import Image

from ..core.config import RegionCaptureOptions
from ..core.enums import RegionCaptureMode
from ..editor.manager import Callbacks, CaptureResult, RegionResult, ShapeManager
from ..shell.client import Window, shell
from .backends import Capture

log = logging.getLogger("sharel.region")


@dataclass
class RegionOutcome:
    result: CaptureResult
    image: Image.Image | None  # cropped/annotated result (None when cancelled or for pickers)
    rect_logical: tuple[int, int, int, int] | None  # selected area in global logical px
    window: Window | None = None  # window under a OneClick / snapped selection
    point_logical: tuple[int, int] | None = None
    color: str | None = None


def snap_rects_for(capture: Capture, windows: list[Window], exclude_pids: set[int]) -> list[tuple]:
    """Window frame + client-area rects in capture-image pixels, topmost first."""
    ox, oy = capture.region[0], capture.region[1]
    s = capture.scale
    out = []
    for w in sorted(windows, key=lambda w: -w.stacking):
        if w.minimized or not w.on_current_workspace or w.pid in exclude_pids or w.type == "DESKTOP":
            continue
        rects = []
        cr = tuple(w.client_rect)
        if cr[2] and cr[3] and cr != tuple(w.frame):
            rects.append(cr)
        rects.append(tuple(w.frame))
        for x, y, ww, hh in rects:
            out.append(((x - ox) * s, (y - oy) * s, ww * s, hh * s))
    return out


class RegionCaptureSession:
    def __init__(self, app, capture: Capture, mode: RegionCaptureMode, options: RegionCaptureOptions,
                 on_done: Callable[[RegionOutcome], None], *, windows: list[Window] | None = None,
                 callbacks: Callbacks | None = None) -> None:
        import os

        from ..ui.annotate import CanvasView, DialogBridge, FixedMapping, Toolbar
        self.app = app
        self.capture = capture
        self.mode = mode
        self.on_done = on_done
        self.windows = windows if windows is not None else self._list_windows()
        self._done = False
        cb = callbacks or Callbacks()
        self.dialogs = DialogBridge(lambda: self.manager, self._active_window, self._redraw)
        cb.request_text = cb.request_text or self.dialogs.request_text
        cb.request_image_file = cb.request_image_file or self.dialogs.request_image_file
        cb.request_sticker = cb.request_sticker or self.dialogs.request_sticker
        cb.on_change = self._changed
        cb.on_result = self._on_result
        self.manager = ShapeManager(capture.image, mode, options, cb,
                                    screen_origin=(capture.region[0], capture.region[1]))
        self.manager.set_snap_rects(snap_rects_for(capture, self.windows, {os.getpid()})
                                    if options.detect_windows else [])
        from .screen import current_layout
        layout = current_layout()
        s = capture.scale
        ox, oy = capture.region[0], capture.region[1]
        self.manager.set_monitors([((m.x - ox) * s, (m.y - oy) * s, m.width * s, m.height * s)
                                   for m in layout.monitors])
        self.views: list = []
        self.gtk_windows: list[Gtk.Window] = []
        self.toolbar = None
        show_toolbar = mode in (RegionCaptureMode.Annotation, RegionCaptureMode.TaskEditor) and \
            not getattr(app.store.app.default_task_settings.advanced_settings, "region_capture_disable_annotation",
                        False)
        pointer = self._pointer()
        display = Gdk.Display.get_default()
        gdk_mons = list(display.get_monitors()) if display else []
        from ..ui.placement import raise_and_focus, unique_title
        for m in layout.monitors:
            gm = self._gdk_monitor(gdk_mons, m)
            win = Gtk.Window(title=unique_title("ShareL region capture"), decorated=False)
            win.set_application(app)
            mapping = FixedMapping((m.x - ox, m.y - oy), s)
            view = CanvasView(self.manager, mapping, on_result=self._on_result)
            overlay = Gtk.Overlay(child=view)
            on_this = pointer is not None and m.x <= pointer[0] < m.x + m.width and m.y <= pointer[1] < m.y + m.height
            if show_toolbar and (on_this or (pointer is None and m.primary)) and self.toolbar is None:
                close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Cancel (Esc)")
                close.add_css_class("flat")
                close.connect("clicked", lambda *_: self._on_result(self.manager.close(RegionResult.Close)))
                self.toolbar = Toolbar(self.manager, self.dialogs, extra_end=[close])
                frame = Gtk.Frame(child=self.toolbar, halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                                  margin_top=8)
                frame.add_css_class("osd")
                frame.add_css_class("background")
                overlay.add_overlay(frame)
            win.set_child(overlay)
            if gm is not None:
                win.fullscreen_on_monitor(gm)
            else:
                win.fullscreen()
            self.views.append(view)
            self.gtk_windows.append(win)
        for v in self.views:
            v.peers = [p for p in self.views if p is not v]
        for win, view in zip(self.gtk_windows, self.views, strict=True):
            win.present()
            view.grab_focus()
        # GNOME won't focus a window opened from a global hotkey on its own: ask the shell.
        target = next((w for w, m in zip(self.gtk_windows, layout.monitors, strict=True)
                       if pointer and m.x <= pointer[0] < m.x + m.width and m.y <= pointer[1] < m.y + m.height),
                      self.gtk_windows[0] if self.gtk_windows else None)
        if target is not None:
            raise_and_focus(target, above=True)
        if pointer is not None:
            px, py = pointer
            self.manager.motion((px - ox) * s, (py - oy) * s)

    # -- helpers --

    @staticmethod
    def _list_windows() -> list[Window]:
        sh = shell()
        if not sh.available:
            return []
        try:
            return sh.get_windows()
        except Exception:
            return []

    @staticmethod
    def _pointer() -> tuple[int, int] | None:
        sh = shell()
        if not sh.available:
            return None
        try:
            x, y, _m = sh.get_pointer()
            return (x, y)
        except Exception:
            return None

    @staticmethod
    def _gdk_monitor(gdk_mons, m):
        for gm in gdk_mons:
            g = gm.get_geometry()
            if (g.x, g.y, g.width, g.height) == (m.x, m.y, m.width, m.height):
                return gm
        return gdk_mons[m.index] if m.index < len(gdk_mons) else None

    def _active_window(self) -> Gtk.Window | None:
        for w in self.gtk_windows:
            if w.is_active():
                return w
        return self.gtk_windows[0] if self.gtk_windows else None

    def _redraw(self) -> None:
        for v in self.views:
            v.queue_draw()

    def _changed(self) -> None:
        self._redraw()
        if self.toolbar is not None:
            self.toolbar.schedule_rebuild()

    # -- results --

    def _on_result(self, res: CaptureResult) -> None:
        if self._done:
            return
        self._done = True
        cap = self.capture
        s = cap.scale
        ox, oy = cap.region[0], cap.region[1]
        img = None
        rect_logical = None
        window = None
        point_logical = None
        if res.result not in (RegionResult.Close, RegionResult.AnnotateCancelTask):
            try:
                img = self.manager.result_image(res)
            except Exception:
                log.exception("result image failed")
                img = None
            if res.rect is not None:
                x, y, w, h = res.rect
                rect_logical = (round(x / s + ox), round(y / s + oy), round(w / s), round(h / s))
        if res.point is not None:
            point_logical = (round(res.point[0] / s + ox), round(res.point[1] / s + oy))
        if res.window_rect is not None or (rect_logical and self.windows):
            probe = point_logical
            if probe is None and rect_logical:
                probe = (rect_logical[0] + 1, rect_logical[1] + 1)
            if probe:
                for w in sorted(self.windows, key=lambda w: -w.stacking):
                    fx, fy, fw, fh = w.frame
                    if not w.minimized and fx <= probe[0] < fx + fw and fy <= probe[1] < fy + fh:
                        window = w
                        break
        for win in self.gtk_windows:
            win.destroy()
        self.gtk_windows.clear()
        self.on_done(RegionOutcome(res, img, rect_logical, window, point_logical, res.color))
