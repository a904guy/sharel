"""Hotkey settings (ShareX's HotkeySettingsForm + HotkeySelectionControl).

One row per hotkey/workflow: enable check, status icon (from ``app.hotkeys.status`` plus our own
conflict check), description and job, a key-capture button and an "Edit task settings" button.
The toolbar adds (job picker grouped like ShareX's task menu), removes, duplicates, moves (wrapping
like ShareX), changes the task, renames and resets. Every change saves HotkeysConfig.json and
re-registers the hotkeys.
"""
from __future__ import annotations

import copy
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ...core.config import HotkeyInfo, HotkeySettings, TaskSettings, default_hotkeys
from ...core.enums import HOTKEY_DESCRIPTIONS, HOTKEY_GROUPS, HotkeyType
from ...shell.hotkeys import find_conflicts, gnome_bindings, label, normalize
from .. import gtkutil
from ._sectioned import WindowRegistry

log = logging.getLogger("sharel.ui.hotkeys")

_REGISTRY = WindowRegistry()

# Keys that only modify; pressing them alone never ends the capture.
MODIFIER_KEYS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L",
                 "Meta_R", "Hyper_L", "Hyper_R", "ISO_Level3_Shift", "ISO_Level5_Shift", "Caps_Lock", "Num_Lock",
                 "Shift_Lock", "ISO_Next_Group", "ISO_Prev_Group"}

STATE_ICONS = {
    "registered": ("object-select-symbolic", "success"),
    "pending": ("content-loading-symbolic", "dim-label"),
    "failed": ("dialog-error-symbolic", "error"),
    "conflict": ("dialog-warning-symbolic", "warning"),
    "warning": ("dialog-warning-symbolic", "warning"),
    "disabled": ("action-unavailable-symbolic", "dim-label"),
    "unset": ("action-unavailable-symbolic", "dim-label"),
    "unregistered": ("action-unavailable-symbolic", "dim-label"),
}
STATE_TEXT = {"registered": "Registered", "pending": "Waiting for the shell", "failed": "Failed",
              "conflict": "Conflict", "warning": "Possible conflict", "disabled": "Disabled", "unset": "No hotkey",
              "unregistered": "Not registered"}


def open_hotkey_settings(app) -> HotkeySettingsWindow:
    return _REGISTRY.get_or_create(id(app), lambda: HotkeySettingsWindow(app))


def job_title(job: HotkeyType) -> str:
    return HOTKEY_DESCRIPTIONS.get(job.value, job.value)


# ---- key capture ------------------------------------------------------------------------------


class HotkeyButton(Gtk.Button):
    """Click → "Press a key…"; the next non-modifier key press (with its modifiers) becomes the
    accelerator. Escape cancels, Backspace clears (ShareX's HotkeySelectionButton)."""

    def __init__(self, accel: str, on_change, on_capture=None) -> None:
        super().__init__(valign=Gtk.Align.CENTER, width_request=180, tooltip_text="Click, then press the hotkey")
        self.accel = accel
        self.capturing = False
        self.on_change = on_change
        self.on_capture = on_capture or (lambda _active: None)
        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(self.key_controller)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.capturing and self.cancel_capture())
        self.add_controller(focus)
        self.connect("clicked", self._on_clicked)
        self._update_label()

    def _update_label(self) -> None:
        if self.capturing:
            self.set_label("Press a key…")
        else:
            self.set_label(label(self.accel) if self.accel else "None")

    def set_accel(self, accel: str) -> None:
        self.accel = accel
        self._update_label()

    def _on_clicked(self, *_a) -> None:
        if self.capturing:
            self.cancel_capture()
        else:
            self.start_capture()

    def start_capture(self) -> None:
        self.capturing = True
        self._update_label()
        self.grab_focus()
        self.on_capture(True)

    def cancel_capture(self) -> None:
        self._end(None)

    def _end(self, accel: str | None) -> None:
        self.capturing = False
        if accel is not None:
            self.accel = accel
        self._update_label()
        self.on_capture(False)
        if accel is not None:
            self.on_change(accel)

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state) -> bool:
        if not self.capturing:
            return False
        mods = Gdk.ModifierType(int(state)) & Gtk.accelerator_get_default_mod_mask()
        name = Gdk.keyval_name(keyval) or ""
        if name in MODIFIER_KEYS:
            return True
        if keyval == Gdk.KEY_Escape and not mods:
            self._end(None)
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete) and not mods:
            self._end("")
            return True
        keyval = Gdk.keyval_to_lower(keyval)
        accel = normalize(Gtk.accelerator_name(keyval, mods))
        self._end(accel)
        return True


# ---- rows ---------------------------------------------------------------------------------------


class HotkeyRow(Gtk.ListBoxRow):
    def __init__(self, win: HotkeySettingsWindow, hk: HotkeySettings) -> None:
        super().__init__()
        self.win = win
        self.hk = hk
        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        self.enabled = Gtk.CheckButton(active=hk.hotkey_info.enabled, valign=Gtk.Align.CENTER,
                                       tooltip_text="Enable this hotkey")
        self.enabled.connect("toggled", self._on_enabled)
        self.status_icon = Gtk.Image(icon_name="action-unavailable-symbolic", valign=Gtk.Align.CENTER)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("heading")
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, wrap=False)
        self.subtitle.add_css_class("dim-label")
        self.subtitle.add_css_class("caption")
        texts.append(self.title)
        texts.append(self.subtitle)
        self.hotkey_button = HotkeyButton(hk.hotkey_info.hotkey, self._on_accel, win.on_capture_state)
        self.edit_button = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                                      tooltip_text="Edit task settings…")
        self.edit_button.add_css_class("flat")
        self.edit_button.connect("clicked", lambda *_: win.edit(self))
        for w in (self.enabled, self.status_icon, texts, self.hotkey_button, self.edit_button):
            box.append(w)
        self.set_child(box)
        self.state = "unregistered"
        self.detail = ""
        self.update_texts()

    def update_texts(self) -> None:
        self.title.set_label(self.hk.name)
        job = job_title(self.hk.job) if self.hk.job.value != "None" else "No task"
        parts = [job if self.hk.task_settings.description else self.hk.job.value]
        parts.append(STATE_TEXT.get(self.state, self.state))
        if self.detail:
            parts.append(self.detail)
        self.subtitle.set_label(" · ".join(parts))
        self.set_tooltip_text(self.detail or None)

    def set_status(self, state: str, detail: str) -> None:
        self.state, self.detail = state, detail
        icon, css = STATE_ICONS.get(state, STATE_ICONS["unregistered"])
        self.status_icon.set_from_icon_name(icon)
        for c in ("success", "error", "warning", "dim-label"):
            self.status_icon.remove_css_class(c)
        self.status_icon.add_css_class(css)
        self.status_icon.set_tooltip_text(STATE_TEXT.get(state, state) + (f": {detail}" if detail else ""))
        self.update_texts()

    def _on_enabled(self, btn) -> None:
        self.hk.hotkey_info.enabled = btn.get_active()
        self.win.commit()

    def _on_accel(self, accel: str) -> None:
        self.hk.hotkey_info.hotkey = accel
        self.win.commit()


# ---- job picker ---------------------------------------------------------------------------------


def job_picker(parent: Gtk.Widget | None, on_pick, title: str = "Choose a task") -> Adw.Dialog:
    """ShareX's task menu: every job grouped as in HOTKEY_GROUPS, with its description; searchable."""
    dlg = Adw.Dialog(title=title, content_width=460, content_height=620)
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    search = Gtk.SearchEntry(placeholder_text="Search tasks", margin_start=12, margin_end=12, margin_top=6)
    page = Adw.PreferencesPage()
    rows: list[tuple[Adw.ActionRow, str]] = []
    groups: list[tuple[Adw.PreferencesGroup, list[Adw.ActionRow]]] = []
    for gname, jobs in HOTKEY_GROUPS.items():
        g = Adw.PreferencesGroup(title=gname)
        grows = []
        for job in jobs:
            desc = HOTKEY_DESCRIPTIONS.get(job, job)
            r = Adw.ActionRow(title=desc, subtitle=job, activatable=True)
            r.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))

            def chosen(_r, job=job):
                dlg.close()
                on_pick(HotkeyType[job])
            r.connect("activated", chosen)
            g.add(r)
            rows.append((r, f"{desc} {job}".lower()))
            grows.append(r)
        page.add(g)
        groups.append((g, grows))

    def filter_rows(entry):
        q = entry.get_text().strip().lower()
        for r, key in rows:
            r.set_visible(not q or q in key)
        for g, grows in groups:
            g.set_visible(any(r.get_visible() for r in grows))
    search.connect("search-changed", filter_rows)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(search)
    box.append(page)
    page.set_vexpand(True)
    view.set_content(box)
    dlg.set_child(view)
    dlg.rows = rows  # type: ignore[attr-defined]
    dlg.search = search  # type: ignore[attr-defined]
    dlg.present(parent)
    return dlg


# ---- the window ---------------------------------------------------------------------------------


class HotkeySettingsWindow(Adw.Window):
    def __init__(self, app) -> None:
        super().__init__(application=app, title="Hotkey settings", default_width=820, default_height=560)
        self.app = app
        self.rows: list[HotkeyRow] = []
        try:
            self._gnome = gnome_bindings()
        except Exception as e:
            log.warning("cannot read GNOME keybindings: %s", e)
            self._gnome = {}
        self._capturing = False

        header = Adw.HeaderBar()
        self.banner = Adw.Banner(title="Hotkeys are disabled", button_label="Enable hotkeys")
        self.banner.connect("button-clicked", lambda *_: self.enable_hotkeys())
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", lambda *_: self._update_buttons())
        self.listbox.connect("row-activated", lambda _lb, row: self.edit(row))
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12, margin_top=12,
                        margin_bottom=12)
        clamp.append(self.listbox)
        scroller.set_child(clamp)

        bar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.buttons: dict[str, Gtk.Button] = {}
        for key, text, cb in (("add", "Add…", self.open_job_picker), ("remove", "Remove", self.remove_selected),
                              ("duplicate", "Duplicate", self.duplicate_selected),
                              ("up", "Move up", lambda: self.move_selected(-1)),
                              ("down", "Move down", lambda: self.move_selected(1)),
                              ("task", "Change task…", self.change_task_selected),
                              ("rename", "Description…", self.rename_selected),
                              ("edit", "Edit…", lambda: self.edit(self.selected_row()))):
            b = Gtk.Button(label=text)
            b.connect("clicked", lambda *_a, cb=cb: cb())
            bar.append(b)
            self.buttons[key] = b
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        reset = Gtk.Button(label="Reset to defaults")
        reset.add_css_class("destructive-action")
        reset.connect("clicked", lambda *_: self.reset_defaults())
        bar.append(reset)
        self.buttons["reset"] = reset
        self.backend_label = Gtk.Label(xalign=0, margin_start=12, margin_bottom=8)
        self.backend_label.add_css_class("dim-label")
        self.backend_label.add_css_class("caption")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(scroller)
        content.append(bar)
        content.append(self.backend_label)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(content)
        self.set_content(view)
        self.rebuild()
        self._timer = GLib.timeout_add_seconds(2, self._tick)
        self.connect("close-request", self._on_close)
        self.connect("destroy", self._on_close)

    # -- list --

    @property
    def hotkeys(self) -> list[HotkeySettings]:
        return self.app.store.hotkeys.hotkeys

    def rebuild(self, select: HotkeySettings | None = None) -> None:
        sel = select or (self.selected_row().hk if self.selected_row() else None)
        self.listbox.remove_all()
        self.rows = []
        for hk in self.hotkeys:
            row = HotkeyRow(self, hk)
            self.listbox.append(row)
            self.rows.append(row)
            if hk is sel:
                self.listbox.select_row(row)
        self.refresh_status()
        self._update_buttons()

    def selected_row(self) -> HotkeyRow | None:
        row = self.listbox.get_selected_row()
        return row if isinstance(row, HotkeyRow) else None

    def select(self, index: int) -> None:
        if 0 <= index < len(self.rows):
            self.listbox.select_row(self.rows[index])

    def _update_buttons(self) -> None:
        has = self.selected_row() is not None
        for k in ("remove", "duplicate", "task", "rename", "edit"):
            self.buttons[k].set_sensitive(has)
        for k in ("up", "down"):
            self.buttons[k].set_sensitive(has and len(self.rows) > 1)

    def refresh_status(self) -> None:
        hks = self.hotkeys
        conflicts = find_conflicts(hks, self._gnome)
        status = {id(st.hotkey): st for st in (self.app.hotkeys.status if self.app.hotkeys else [])}
        for i, row in enumerate(self.rows):
            info = row.hk.hotkey_info
            st = status.get(id(row.hk))
            if not info.hotkey:
                state, detail = "unset", ""
            elif not info.enabled:
                state, detail = "disabled", ""
            elif i in conflicts and conflicts[i].startswith("also used"):
                state, detail = "conflict", conflicts[i]
            elif st is not None and st.state in ("failed", "conflict"):
                state, detail = st.state, st.detail or conflicts.get(i, "")
            elif i in conflicts:
                state = st.state if st is not None and st.state == "registered" else "warning"
                detail = conflicts[i]
            elif st is not None:
                state, detail = st.state, st.detail
            else:
                state, detail = "unregistered", ""
            row.set_status(state, detail)
        disabled = bool(self.app.store.app.disable_hotkeys)
        self.banner.set_revealed(disabled)
        backend = self.app.hotkeys.active_backend if self.app.hotkeys else ""
        pref = self.app.store.app.hotkey_backend.value
        self.backend_label.set_label(f"Hotkey backend: {backend or 'none active'} (setting: {pref})")

    def _tick(self) -> bool:
        if self._capturing:
            return GLib.SOURCE_CONTINUE
        if self.app.hotkeys:
            self.app.hotkeys.refresh_states()
        self.refresh_status()
        return GLib.SOURCE_CONTINUE

    def _on_close(self, *_a) -> bool:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._capturing:
            self._capturing = False
            self.app.register_hotkeys()
        return False

    # -- saving --

    def commit(self) -> None:
        """Save HotkeysConfig.json, re-register, refresh rows."""
        self.app.store.save_hotkeys()
        self.app.register_hotkeys()
        self.app.settings_changed()
        for row in self.rows:
            row.update_texts()
        self.refresh_status()

    def on_capture_state(self, active: bool) -> None:
        """While a key is being captured the global grabs are released, so the key reaches us."""
        self._capturing = active
        if active:
            if self.app.hotkeys:
                self.app.hotkeys.unregister()
        else:
            self.app.register_hotkeys()
            self.refresh_status()

    def enable_hotkeys(self) -> None:
        self.app.store.app.disable_hotkeys = False
        self.app.store.save_app()
        self.app.register_hotkeys()
        self.app.settings_changed()
        self.refresh_status()

    # -- actions --

    def open_job_picker(self) -> Adw.Dialog:
        return job_picker(self, self.add_hotkey, "Add hotkey: choose a task")

    def add_hotkey(self, job: HotkeyType, accel: str = "") -> HotkeySettings:
        hk = HotkeySettings(HotkeyInfo(accel), TaskSettings(job=job))
        self.hotkeys.append(hk)
        self.rebuild(select=hk)
        self.commit()
        return hk

    def remove_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        idx = self.rows.index(row)
        self.hotkeys.remove(row.hk)
        self.rebuild()
        self.select(min(idx, len(self.rows) - 1))
        self.commit()

    def duplicate_selected(self) -> HotkeySettings | None:
        row = self.selected_row()
        if row is None:
            return None
        ts = copy.deepcopy(row.hk.task_settings)
        ts.watch_folder_enabled = False  # like ShareX: a copy never watches the same folders
        ts.watch_folder_list = []
        # Like ShareX only the task settings are copied: the copy starts without a hotkey.
        hk = HotkeySettings(HotkeyInfo("", row.hk.hotkey_info.enabled), ts)
        self.hotkeys.insert(self.hotkeys.index(row.hk) + 1, hk)
        self.rebuild(select=hk)
        self.commit()
        return hk

    def move_selected(self, delta: int) -> None:
        row = self.selected_row()
        if row is None or len(self.hotkeys) < 2:
            return
        i = self.hotkeys.index(row.hk)
        j = (i + delta) % len(self.hotkeys)  # ShareX wraps around at both ends
        hk = self.hotkeys.pop(i)
        self.hotkeys.insert(j, hk)
        self.rebuild(select=hk)
        self.commit()

    def change_task_selected(self) -> Adw.Dialog | None:
        row = self.selected_row()
        if row is None:
            return None

        def picked(job: HotkeyType):
            row.hk.task_settings.job = job
            self.commit()
        return job_picker(self, picked, "Change task")

    def rename_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            return

        def done(text):
            if text is None:
                return
            row.hk.task_settings.description = text.strip()
            self.commit()
        gtkutil.prompt_text(self, "Description", row.hk.task_settings.description, done,
                            body="A name for this workflow (also usable with sharel -workflow \"name\"). "
                                 "Leave empty to use the task's name.")

    def edit(self, row: HotkeyRow | None):
        if row is None:
            return None
        from .tasksettings import open_task_settings
        return open_task_settings(self.app, row.hk, on_close=self._after_edit)

    def _after_edit(self) -> None:
        if self.get_visible():
            self.commit()

    def reset_defaults(self) -> None:
        def done(resp):
            if resp == "reset":
                self.do_reset()
        gtkutil.ask(self, "Reset hotkeys?", "Replace every hotkey with ShareL's defaults.",
                    [("cancel", "Cancel"), ("reset", "Reset")], done, destructive="reset")

    def do_reset(self) -> None:
        self.hotkeys[:] = default_hotkeys()
        self.rebuild()
        self.commit()
