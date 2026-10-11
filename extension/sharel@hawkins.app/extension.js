// ShareL: gives the ShareL app what a Wayland client can't do itself (see dbus.js).

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import System from 'system';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {BUS_NAME, OBJECT_PATH, INTERFACE_XML} from './dbus.js';
import * as L from './logic.js';

const VERSION = '0.1.0';
// A seat without a keymap yet (headless shell, early login) can't resolve accelerators, so a
// failed grab is retried; only after this many attempts is it reported as failed (a conflict).
const GRAB_RETRY_MS = 2000;
const GRAB_MAX_ATTEMPTS = 30;

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');

const TYPE_NAMES = Object.fromEntries(Object.entries(Meta.WindowType).map(([k, v]) => [v, k]));

function rectArray(r) {
    return [r.x, r.y, r.width, r.height];
}

// The client area: the frame without server-side decorations (Wayland CSD windows: the frame).
function clientRect(win) {
    try {
        return rectArray(win.frame_rect_to_client_rect(win.get_frame_rect()));
    } catch {
        return rectArray(win.get_frame_rect());
    }
}

function windowInfo(win, stacking = -1) {
    const app = Shell.WindowTracker.get_default().get_window_app(win);
    const ws = win.get_workspace();
    const active = global.workspace_manager.get_active_workspace();
    let maximized = false;
    try {
        maximized = win.is_maximized();
    } catch {
        maximized = win.get_maximized?.() === Meta.MaximizeFlags.BOTH;
    }
    return {
        id: Number(win.get_id()),
        title: win.get_title() ?? '',
        wm_class: win.get_wm_class() ?? '',
        wm_class_instance: win.get_wm_class_instance?.() ?? '',
        app_id: app ? app.get_id() : '',
        app_name: app ? app.get_name() : '',
        gtk_app_id: win.get_gtk_application_id?.() ?? '',
        pid: win.get_pid(),
        client: win.get_client_type() === Meta.WindowClientType.X11 ? 'x11' : 'wayland',
        type: TYPE_NAMES[win.get_window_type()] ?? String(win.get_window_type()),
        frame: rectArray(win.get_frame_rect()),
        buffer: rectArray(win.get_buffer_rect()),
        client_rect: clientRect(win),
        monitor: win.get_monitor(),
        workspace: win.is_on_all_workspaces() ? -1 : (ws ? ws.index() : -1),
        on_current_workspace: win.is_on_all_workspaces() || ws === active,
        minimized: win.minimized,
        maximized,
        fullscreen: win.is_fullscreen(),
        above: win.is_above(),
        focus: win.has_focus(),
        stacking,
    };
}

function listedWindows() {
    const wins = global.display.list_all_windows().filter(w => !w.is_override_redirect());
    const sorted = global.display.sort_windows_by_stacking(wins);
    return sorted.map((w, i) => [w, i]).filter(([w]) => L.LISTED_TYPES.has(TYPE_NAMES[w.get_window_type()]));
}

function findWindow(id) {
    const want = BigInt(id);
    return global.display.list_all_windows().find(w => BigInt(w.get_id()) === want) ?? null;
}

function json(obj) {
    return JSON.stringify(obj);
}

class ShareLService {
    constructor() {
        this._accels = new L.AcceleratorRegistry();
        this._signals = [];
        this._timeouts = new Set();
        this._borders = new Map();
        this._highlighter = null;
        this._borderless = new Map(); // window id -> how it was made borderless
        this._gcTimeout = 0;
        this._grabRetry = 0;
    }

    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE, null, null);
        this._connect(global.display, 'accelerator-activated', (_d, action, _device, timestamp) => {
            const e = this._accels.byAction(action);
            if (e) {
                this._dbus?.emit_signal('AcceleratorActivated',
                    new GLib.Variant('(usu)', [e.handle, e.name, timestamp >>> 0]));
            }
        });
        // A keyboard appearing (a real one, or a virtual one from a remote-desktop session)
        // brings a keymap: retry pending grabs right away.
        const seat = Clutter.get_default_backend().get_default_seat();
        this._connect(seat, 'device-added', () => this._retryGrabs());
    }

    disable() {
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];
        for (const id of this._timeouts)
            GLib.source_remove(id);
        this._timeouts.clear();
        if (this._gcTimeout)
            GLib.source_remove(this._gcTimeout);
        this._gcTimeout = 0;
        this._grabRetry = 0;
        this.UngrabAll();
        this._stopHighlighter();
        if (this._trayActor && this._trayHandler)
            this._trayActor.disconnect(this._trayHandler);
        this._trayActor = null;
        this._trayWatch = 0;
        for (const actor of this._borders.values())
            actor.destroy();
        this._borders.clear();
        this._pick?.finish({cancelled: true});
        if (this._nameId)
            Gio.bus_unown_name(this._nameId);
        this._nameId = 0;
        this._dbus?.unexport();
        this._dbus = null;
    }

    _connect(obj, signal, fn) {
        this._signals.push([obj, obj.connect(signal, fn)]);
    }

    _timeout(ms, fn) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timeouts.delete(id);
            fn();
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(id);
        return id;
    }

    // Screenshots allocate big native buffers behind small JS objects; collect soon after.
    _releaseCaptureMemorySoon() {
        if (this._gcTimeout)
            GLib.source_remove(this._gcTimeout);
        this._gcTimeout = GLib.timeout_add(GLib.PRIORITY_LOW, 1000, () => {
            this._gcTimeout = 0;
            System.gc();
            return GLib.SOURCE_REMOVE;
        });
    }

    get Version() {
        return VERSION;
    }

    // ---- windows / monitors -------------------------------------------------------

    GetMonitors() {
        const primary = global.display.get_primary_monitor();
        return json(Main.layoutManager.monitors.map(m => ({
            index: m.index, x: m.x, y: m.y, width: m.width, height: m.height,
            scale: global.display.get_monitor_scale(m.index), geometry_scale: m.geometry_scale,
            primary: m.index === primary,
        })));
    }

    GetWindows() {
        return json(listedWindows().map(([w, i]) => windowInfo(w, i)));
    }

    GetActiveWindow() {
        const win = global.display.focus_window;
        if (!win)
            return json({});
        const entry = listedWindows().find(([w]) => w === win);
        return json(windowInfo(win, entry ? entry[1] : -1));
    }

    GetWindowDetails(id) {
        const win = findWindow(id);
        if (!win)
            return json({});
        const info = windowInfo(win);
        const actor = win.get_compositor_private();
        Object.assign(info, {
            description: win.get_description() ?? '',
            role: win.get_role?.() ?? '',
            sandboxed_app_id: win.get_sandboxed_app_id?.() ?? '',
            startup_id: win.get_startup_id?.() ?? '',
            layer: win.get_layer?.() ?? -1,
            decorated: win.decorated ?? true,
            sticky: win.is_on_all_workspaces(),
            skip_taskbar: win.skip_taskbar ?? false,
            resizeable: win.resizeable ?? true,
            opacity: actor ? actor.get_opacity() : 255,
            transient_for: win.get_transient_for() ? Number(win.get_transient_for().get_id()) : 0,
            user_time: win.get_user_time(),
            borderless: this._borderless.get(Number(win.get_id())) ?? '',
        });
        return json(info);
    }

    GetPointer() {
        const [x, y, mods] = global.get_pointer();
        return [x, y, mods];
    }

    Activate(id) {
        const win = findWindow(id);
        if (!win)
            return false;
        win.activate(global.get_current_time());
        return true;
    }

    SetAbove(id, above) {
        const win = findWindow(id);
        if (!win)
            return false;
        if (above)
            win.make_above();
        else
            win.unmake_above();
        return true;
    }

    ToggleAbove(id) {
        const win = findWindow(id);
        if (!win)
            return false;
        if (win.is_above())
            win.unmake_above();
        else
            win.make_above();
        return win.is_above();
    }

    MoveResize(id, x, y, width, height) {
        const win = findWindow(id);
        if (!win)
            return json({ok: false, error: 'no such window'});
        if (win.is_fullscreen())
            win.unmake_fullscreen();
        if (win.get_maximized?.())
            win.unmaximize?.(Meta.MaximizeFlags.BOTH);
        win.move_resize_frame(true, x, y, width, height);
        return json({ok: true, frame: rectArray(win.get_frame_rect())});
    }

    PositionWindow(id, x, y) {
        const win = findWindow(id);
        if (!win)
            return false;
        win.move_frame(true, x, y);
        return true;
    }

    FindWindows(pid, title) {
        return json(listedWindows()
            .filter(([w]) => (!pid || w.get_pid() === pid) && (!title || (w.get_title() ?? '') === title))
            .map(([w, i]) => windowInfo(w, i)));
    }

    MakeBorderless(id, borderless) {
        const win = findWindow(id);
        if (!win)
            return json({ok: false, error: 'no such window'});
        const client = win.get_client_type() === Meta.WindowClientType.X11 ? 'x11' : 'wayland';
        const method = L.borderlessMethod(client);
        const wid = Number(win.get_id());
        if (method === 'motif-hints') {
            const xid = win.get_description();
            const hints = borderless ? '2, 0, 0, 0, 0' : '2, 0, 1, 0, 0';
            try {
                const proc = Gio.Subprocess.new(['xprop', '-id', xid, '-f', '_MOTIF_WM_HINTS', '32c',
                    '-set', '_MOTIF_WM_HINTS', hints], Gio.SubprocessFlags.NONE);
                proc.wait(null);
            } catch (e) {
                return json({ok: false, error: String(e), method});
            }
        } else if (borderless) {
            win.make_fullscreen();
        } else {
            win.unmake_fullscreen();
        }
        if (borderless)
            this._borderless.set(wid, method);
        else
            this._borderless.delete(wid);
        return json({ok: true, method});
    }

    MakeFullscreen(id, fullscreen) {
        const win = findWindow(id);
        if (!win)
            return false;
        if (fullscreen)
            win.make_fullscreen();
        else
            win.unmake_fullscreen();
        return true;
    }

    SetOpacity(id, opacity) {
        const actor = findWindow(id)?.get_compositor_private();
        if (!actor)
            return false;
        actor.set_opacity(Math.max(0, Math.min(255, opacity)));
        return true;
    }

    SetSticky(id, sticky) {
        const win = findWindow(id);
        if (!win)
            return false;
        if (sticky)
            win.stick();
        else
            win.unstick();
        return true;
    }

    // ---- capture --------------------------------------------------------------------

    async ScreenshotAsync([kind, target, x, y, width, height, cursor, path], invocation) {
        try {
            const res = kind === 'window'
                ? await this._shootWindow(Number(target), cursor, path)
                : await this._shootStage(kind, Number(target), [x, y, width, height], cursor, path);
            this._releaseCaptureMemorySoon();
            invocation.return_value(new GLib.Variant('(s)', [json({ok: true, path, ...res})]));
        } catch (e) {
            invocation.return_value(new GLib.Variant('(s)', [json({ok: false, error: String(e)})]));
        }
    }

    async _shootStage(kind, target, area, cursor, path) {
        const shooter = new Shell.Screenshot();
        const [content, scale, cursorContent, cursorPoint, cursorScale] =
            await shooter.screenshot_stage_to_content();
        let logical = null;
        if (kind === 'area') {
            logical = area;
        } else if (kind === 'monitor') {
            const m = Main.layoutManager.monitors[target] ?? Main.layoutManager.primaryMonitor;
            logical = [m.x, m.y, m.width, m.height];
        }
        const geometry = logical ? L.scaleRect(logical, scale) : [0, 0, -1, -1];
        const cursorTexture = cursor && cursorContent ? cursorContent.get_texture() : null;
        const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION,
            null);
        await Shell.Screenshot.composite_to_stream(content.get_texture(), ...geometry, scale,
            cursorTexture, cursorPoint ? cursorPoint.x * scale : 0, cursorPoint ? cursorPoint.y * scale : 0,
            cursorScale ?? 1, stream);
        stream.close(null);
        return {kind, scale, area: logical ?? [0, 0, global.stage.width, global.stage.height],
            cursor: cursorPoint ? [cursorPoint.x, cursorPoint.y] : null};
    }

    async _shootWindow(id, cursor, path) {
        const win = findWindow(id);
        if (!win)
            throw new Error('no such window');
        const actor = win.get_compositor_private();
        const content = actor.paint_to_content(null);
        const texture = content.get_texture();
        const scale = actor.get_resource_scale();
        const frame = rectArray(win.get_frame_rect());
        const buffer = rectArray(win.get_buffer_rect());
        let cursorTexture = null, cx = 0, cy = 0, cscale = 1;
        if (cursor) {
            const [, , cContent, cPoint, cScale] = await new Shell.Screenshot().screenshot_stage_to_content();
            if (cContent && cPoint && L.rectContains(buffer, cPoint.x, cPoint.y)) {
                cursorTexture = cContent.get_texture();
                cx = (cPoint.x - buffer[0]) * scale;
                cy = (cPoint.y - buffer[1]) * scale;
                cscale = cScale;
            }
        }
        const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION,
            null);
        await Shell.Screenshot.composite_to_stream(texture, 0, 0, -1, -1, scale, cursorTexture, cx, cy, cscale,
            stream);
        stream.close(null);
        return {kind: 'window', scale, frame, buffer, frame_in_buffer: L.frameCropInBuffer(frame, buffer, scale),
            window: windowInfo(win)};
    }

    PickWindowAsync([hint], invocation) {
        if (this._pick) {
            invocation.return_value(new GLib.Variant('(s)', [json({cancelled: true, error: 'busy'})]));
            return;
        }
        this._pick = new WindowPicker(hint, result => {
            this._pick = null;
            invocation.return_value(new GLib.Variant('(s)', [json(result)]));
        });
    }

    // ---- clipboard --------------------------------------------------------------------

    SetClipboard(mime, data) {
        const bytes = data instanceof Uint8Array ? data : Uint8Array.from(data);
        const clip = St.Clipboard.get_default();
        if (mime.startsWith('text/'))
            clip.set_text(St.ClipboardType.CLIPBOARD, new TextDecoder().decode(bytes));
        else
            clip.set_content(St.ClipboardType.CLIPBOARD, mime, new GLib.Bytes(bytes));
        return true;
    }

    GetClipboardAsync([mime], invocation) {
        const clip = St.Clipboard.get_default();
        const reply = bytes => invocation.return_value(new GLib.Variant('(ay)', [bytes]));
        if (mime.startsWith('text/plain')) {
            clip.get_text(St.ClipboardType.CLIPBOARD, (_c, text) => reply(new TextEncoder().encode(text ?? '')));
            return;
        }
        clip.get_content(St.ClipboardType.CLIPBOARD, mime, (_c, bytes) =>
            reply(bytes ? bytes.toArray() : new Uint8Array(0)));
    }

    GetClipboardMimetypes() {
        return St.Clipboard.get_default().get_mimetypes(St.ClipboardType.CLIPBOARD);
    }

    // ---- overlays -------------------------------------------------------------------------

    MouseHighlighter(on, style) {
        this._stopHighlighter();
        if (!on)
            return true;
        const s = L.highlighterStyle(style);
        const actor = new St.Widget({reactive: false, can_focus: false, track_hover: false});
        Main.layoutManager.uiGroup.add_child(actor);
        Main.layoutManager.uiGroup.set_child_above_sibling(actor, null);
        let lastPressed = null;
        const tick = () => {
            const [x, y, mods] = global.get_pointer();
            const pressed = (mods & (Clutter.ModifierType.BUTTON1_MASK | Clutter.ModifierType.BUTTON2_MASK |
                Clutter.ModifierType.BUTTON3_MASK)) !== 0;
            if (pressed !== lastPressed) {
                const {size, css} = L.highlighterCss(s, pressed);
                actor.set_style(css);
                actor.set_size(size, size);
                lastPressed = pressed;
            }
            const [w, h] = actor.get_size();
            actor.set_position(Math.round(x - w / 2), Math.round(y - h / 2));
            return GLib.SOURCE_CONTINUE;
        };
        tick();
        const id = GLib.timeout_add(GLib.PRIORITY_HIGH, 16, tick);
        this._highlighter = {actor, id};
        return true;
    }

    _stopHighlighter() {
        if (this._highlighter) {
            GLib.source_remove(this._highlighter.id);
            this._highlighter.actor.destroy();
            this._highlighter = null;
        }
    }

    ShowBorder(tag, x, y, width, height, style) {
        this.HideBorder(tag);
        const s = L.borderStyle(style);
        const [bx, by, bw, bh] = L.outsideBorderRect([x, y, width, height], s.width);
        const actor = new St.Widget({
            reactive: false,
            style: `border: ${s.width}px ${s.dash ? 'dashed' : 'solid'} ${s.color};`,
        });
        actor.set_position(bx, by);
        actor.set_size(bw, bh);
        Main.layoutManager.uiGroup.add_child(actor);
        Main.layoutManager.uiGroup.set_child_above_sibling(actor, null);
        this._borders.set(tag, actor);
        return true;
    }

    HideBorder(tag) {
        const actor = this._borders.get(tag);
        if (!actor)
            return false;
        actor.destroy();
        this._borders.delete(tag);
        return true;
    }

    // ---- tray -----------------------------------------------------------------------------
    // The ubuntu-appindicators extension opens an indicator's menu on every left click. ShareX
    // runs a configurable action instead, so ShareL's indicator gets its button presses here first:
    // left / double / middle click are reported as TrayClicked (left passes through to the menu when
    // the configured action is "open tray menu"; right click always opens the menu).

    _findIndicator(id) {
        for (const actor of Object.values(Main.panel.statusArea)) {
            const ind = actor?._indicator;
            if (ind && (ind.id === id || ind._id === id))
                return actor;
        }
        return null;
    }

    ConfigureTray(id, leftOpensMenu) {
        this._trayId = id;
        this._trayLeftOpensMenu = leftOpensMenu;
        this._hookTray();
        if (!this._trayWatch) {
            // The indicator appears asynchronously (and again after the helper restarts).
            this._trayWatch = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
                this._hookTray();
                return GLib.SOURCE_CONTINUE;
            });
            this._timeouts.add(this._trayWatch);
        }
        return this._findIndicator(id) !== null;
    }

    _hookTray() {
        const actor = this._trayId ? this._findIndicator(this._trayId) : null;
        if (!actor || actor === this._trayActor)
            return;
        if (this._trayActor && this._trayHandler)
            this._trayActor.disconnect(this._trayHandler);
        this._trayActor = actor;
        let pendingLeft = 0;
        this._trayHandler = actor.connect('button-press-event', (_a, event) => {
            const button = event.get_button();
            const emit = which => this._dbus?.emit_signal('TrayClicked', new GLib.Variant('(s)', [which]));
            if (button === Clutter.BUTTON_MIDDLE) {
                emit('middle');
                return Clutter.EVENT_STOP;
            }
            if (button !== Clutter.BUTTON_PRIMARY || this._trayLeftOpensMenu)
                return Clutter.EVENT_PROPAGATE;
            if (pendingLeft) {
                GLib.source_remove(pendingLeft);
                this._timeouts.delete(pendingLeft);
                pendingLeft = 0;
                emit('double');
                return Clutter.EVENT_STOP;
            }
            const {doubleClickTime} = Clutter.Settings.get_default();
            pendingLeft = this._timeout(doubleClickTime, () => {
                pendingLeft = 0;
                emit('left');
            });
            return Clutter.EVENT_STOP;
        });
        actor.connect('destroy', () => {
            if (this._trayActor === actor) {
                this._trayActor = null;
                this._trayHandler = 0;
            }
        });
    }

    GetTrayIconRect(id) {
        const actor = this._findIndicator(id);
        if (!actor)
            return json({found: false});
        const [x, y] = actor.get_transformed_position();
        const [w, h] = actor.get_transformed_size();
        return json({found: true, rect: [Math.round(x), Math.round(y), Math.round(w), Math.round(h)],
            menu_open: Boolean(actor.menu?.isOpen)});
    }

    ToggleTrayMenu(id) {
        const actor = this._findIndicator(id);
        if (!actor?.menu)
            return false;
        actor.menu.toggle();
        return true;
    }

    // ---- hotkeys ------------------------------------------------------------------------

    GrabAccelerator(accelerator, name) {
        const dup = L.findDuplicate(this._accels, accelerator);
        if (dup)
            this.UngrabAccelerator(dup.handle);
        const handle = this._accels.add(accelerator, name);
        this._tryGrab(handle);
        if (this._accels.get(handle).state === 'pending')
            this._scheduleGrabRetry();
        return handle;
    }

    _tryGrab(handle) {
        const e = this._accels.get(handle);
        if (!e || e.state !== 'pending')
            return;
        let action = Meta.KeyBindingAction.NONE;
        try {
            action = global.display.grab_accelerator(e.accelerator, Meta.KeyBindingFlags.NONE);
        } catch {
            action = Meta.KeyBindingAction.NONE;
        }
        if (action !== Meta.KeyBindingAction.NONE) {
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action),
                Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW | Shell.ActionMode.POPUP);
        }
        this._accels.attempted(handle, action !== Meta.KeyBindingAction.NONE ? action : 0, GRAB_MAX_ATTEMPTS);
    }

    _retryGrabs() {
        for (const e of this._accels.pending())
            this._tryGrab(e.handle);
    }

    _scheduleGrabRetry() {
        if (this._grabRetry)
            return;
        this._grabRetry = this._timeout(GRAB_RETRY_MS, () => {
            this._grabRetry = 0;
            this._retryGrabs();
            if (this._accels.pending().length)
                this._scheduleGrabRetry();
        });
    }

    UngrabAccelerator(handle) {
        const e = this._accels.remove(handle);
        if (!e)
            return false;
        if (e.state === 'grabbed')
            global.display.ungrab_accelerator(e.action);
        return true;
    }

    UngrabAll() {
        for (const e of this._accels.clear()) {
            if (e.state === 'grabbed')
                global.display.ungrab_accelerator(e.action);
        }
        return true;
    }

    ListAccelerators() {
        return json(this._accels.all().map(({handle, accelerator, name, state, attempts}) =>
            ({handle, accelerator, name, state, attempts})));
    }
}

// Click a window to pick it (CustomWindow): the window under the pointer is outlined,
// Escape or a right click cancels.
class WindowPicker {
    constructor(hint, done) {
        this._done = done;
        this._finished = false;
        this._overlay = new St.Widget({reactive: true, can_focus: true, x: 0, y: 0,
            width: global.stage.width, height: global.stage.height,
            style: 'background-color: rgba(0,0,0,0.12);'});
        this._outline = new St.Widget({reactive: false,
            style: 'border: 3px solid rgba(53,132,228,0.95); background-color: rgba(53,132,228,0.18);'});
        this._label = new St.Label({text: hint || 'Click a window to select it. Esc to cancel.',
            style: 'background-color: rgba(0,0,0,0.75); color: white; padding: 6px 12px; border-radius: 6px;' +
                ' font-size: 14px;'});
        this._overlay.add_child(this._outline);
        this._overlay.add_child(this._label);
        Main.layoutManager.uiGroup.add_child(this._overlay);
        Main.layoutManager.uiGroup.set_child_above_sibling(this._overlay, null);
        const m = Main.layoutManager.primaryMonitor;
        this._label.set_position(m.x + 20, m.y + 20);
        this._grab = Main.pushModal(this._overlay, {actionMode: Shell.ActionMode.POPUP});
        this._overlay.grab_key_focus();
        this._overlay.connect('motion-event', (_a, event) => {
            this._update(...event.get_coords());
            return Clutter.EVENT_STOP;
        });
        this._overlay.connect('button-press-event', (_a, event) => {
            if (event.get_button() === Clutter.BUTTON_SECONDARY) {
                this.finish({cancelled: true});
                return Clutter.EVENT_STOP;
            }
            const [x, y] = event.get_coords();
            const win = this._windowAt(x, y);
            this.finish(win ? {cancelled: false, window: win} : {cancelled: true});
            return Clutter.EVENT_STOP;
        });
        this._overlay.connect('key-press-event', (_a, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape)
                this.finish({cancelled: true});
            return Clutter.EVENT_STOP;
        });
        const [x, y] = global.get_pointer();
        this._update(x, y);
    }

    _windowAt(x, y) {
        const infos = listedWindows().map(([w, i]) => windowInfo(w, i)).filter(w => w.type !== 'DOCK');
        return L.windowAtPoint(infos, x, y);
    }

    _update(x, y) {
        const win = this._windowAt(x, y);
        if (win) {
            const [fx, fy, fw, fh] = win.frame;
            this._outline.set_position(fx, fy);
            this._outline.set_size(fw, fh);
            this._outline.show();
        } else {
            this._outline.hide();
        }
    }

    finish(result) {
        if (this._finished)
            return;
        this._finished = true;
        if (this._grab)
            Main.popModal(this._grab);
        this._grab = null;
        this._overlay.destroy();
        this._done(result);
    }
}

export default class ShareLExtension extends Extension {
    enable() {
        this._service = new ShareLService();
        this._service.enable();
    }

    disable() {
        this._service?.disable();
        this._service = null;
    }
}
