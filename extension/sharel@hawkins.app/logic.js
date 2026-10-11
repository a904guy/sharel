// Pure logic for the ShareL extension: no gi:// imports, so it runs under plain `gjs -m`
// in unit tests (tests/js/test_logic.js).

/** Window types worth listing/snapping to (Meta.WindowType values by name). */
export const LISTED_TYPES = new Set(['NORMAL', 'DIALOG', 'MODAL_DIALOG', 'UTILITY', 'TOOLBAR', 'MENU',
    'SPLASHSCREEN', 'DOCK']);

export function rectContains([x, y, w, h], px, py) {
    return px >= x && py >= y && px < x + w && py < y + h;
}

export function rectIntersects(a, b) {
    return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
}

export function unionRect(rects) {
    if (!rects.length)
        return [0, 0, 0, 0];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y, w, h] of rects) {
        x0 = Math.min(x0, x);
        y0 = Math.min(y0, y);
        x1 = Math.max(x1, x + w);
        y1 = Math.max(y1, y + h);
    }
    return [x0, y0, x1 - x0, y1 - y0];
}

/**
 * The topmost window under a point. `windows` are info objects (see windowInfo in
 * extension.js) with `stacking` (higher = on top), `frame`, `minimized`, `on_current_workspace`.
 */
export function windowAtPoint(windows, px, py, excludeIds = []) {
    const skip = new Set(excludeIds.map(String));
    return windows
        .filter(w => !w.minimized && w.on_current_workspace !== false && !skip.has(String(w.id)) &&
            rectContains(w.frame, px, py))
        .sort((a, b) => b.stacking - a.stacking)[0] ?? null;
}

export function monitorAt(monitors, px, py) {
    const m = monitors.find(mon => rectContains([mon.x, mon.y, mon.width, mon.height], px, py));
    return m ?? monitors.find(mon => mon.primary) ?? monitors[0] ?? null;
}

/** Scale a logical rect to the texture's pixel grid (GNOME's screenshot UI does the same). */
export function scaleRect([x, y, w, h], scale) {
    return [Math.round(x * scale), Math.round(y * scale), Math.round(w * scale), Math.round(h * scale)];
}

/** Crop rect inside a window buffer texture that keeps only the frame (no client-side shadow). */
export function frameCropInBuffer(frame, buffer, scale) {
    return scaleRect([frame[0] - buffer[0], frame[1] - buffer[1], frame[2], frame[3]], scale);
}

export const HIGHLIGHTER_DEFAULTS = {
    size: 60, color: 'rgba(255,215,0,0.45)', border_color: 'rgba(255,170,0,0.9)', border_width: 2,
    click_color: 'rgba(255,60,60,0.55)', click_scale: 0.8, shape: 'circle',
};

export function highlighterStyle(json) {
    let user = {};
    if (json) {
        try {
            user = JSON.parse(json);
        } catch {
            user = {};
        }
    }
    const s = {...HIGHLIGHTER_DEFAULTS, ...user};
    s.size = Math.max(8, Math.min(400, Number(s.size) || HIGHLIGHTER_DEFAULTS.size));
    s.border_width = Math.max(0, Math.min(40, Number(s.border_width) || 0));
    s.click_scale = Math.max(0.2, Math.min(3, Number(s.click_scale) || 1));
    return s;
}

/** St CSS for the mouse highlighter circle in a given pressed state. */
export function highlighterCss(style, pressed) {
    const size = Math.round(pressed ? style.size * style.click_scale : style.size);
    const radius = style.shape === 'square' ? 0 : Math.round(size / 2);
    const bg = pressed ? style.click_color : style.color;
    return {size, css: `background-color: ${bg}; border-radius: ${radius}px; ` +
        `border: ${style.border_width}px solid ${style.border_color};`};
}

export const BORDER_DEFAULTS = {color: 'rgba(224,27,36,0.95)', width: 2, dash: false};

export function borderStyle(json) {
    let user = {};
    if (json) {
        try {
            user = JSON.parse(json);
        } catch {
            user = {};
        }
    }
    const s = {...BORDER_DEFAULTS, ...user};
    s.width = Math.max(1, Math.min(20, Number(s.width) || BORDER_DEFAULTS.width));
    return s;
}

/** The border is drawn just OUTSIDE the recorded area so it never ends up in the recording. */
export function outsideBorderRect([x, y, w, h], width) {
    return [x - width, y - width, w + 2 * width, h + 2 * width];
}

/**
 * Accelerators the app asked for. Handles are ShareL's own ids (stable across retries of the
 * underlying grab, which fails until the seat has a keymap, e.g. in a headless shell).
 */
export class AcceleratorRegistry {
    constructor() {
        this._next = 1;
        this._byHandle = new Map();
    }

    add(accelerator, name) {
        const handle = this._next++;
        this._byHandle.set(handle, {handle, accelerator, name, action: 0, state: 'pending', attempts: 0});
        return handle;
    }

    get(handle) {
        return this._byHandle.get(handle) ?? null;
    }

    remove(handle) {
        const e = this._byHandle.get(handle) ?? null;
        this._byHandle.delete(handle);
        return e;
    }

    byAction(action) {
        for (const e of this._byHandle.values()) {
            if (e.action === action && e.state === 'grabbed')
                return e;
        }
        return null;
    }

    pending() {
        return [...this._byHandle.values()].filter(e => e.state === 'pending');
    }

    all() {
        return [...this._byHandle.values()];
    }

    /** Record a grab attempt. After maxAttempts failures the state becomes 'failed'. */
    attempted(handle, action, maxAttempts) {
        const e = this._byHandle.get(handle);
        if (!e)
            return null;
        e.attempts += 1;
        if (action) {
            e.action = action;
            e.state = 'grabbed';
        } else if (e.attempts >= maxAttempts) {
            e.state = 'failed';
        }
        return e;
    }

    clear() {
        const all = this.all();
        this._byHandle.clear();
        return all;
    }
}

/** A conflicting duplicate accelerator (same string, case-insensitive) already registered. */
export function findDuplicate(registry, accelerator) {
    const want = normalizeAccelerator(accelerator);
    return registry.all().find(e => normalizeAccelerator(e.accelerator) === want) ?? null;
}

/** "<Ctrl><shift>print" and "<Shift><Control>Print" compare equal. */
export function normalizeAccelerator(accel) {
    const mods = [];
    let rest = String(accel ?? '');
    const re = /^<([^>]+)>/;
    let m;
    while ((m = re.exec(rest))) {
        let mod = m[1].toLowerCase();
        if (mod === 'ctrl' || mod === 'primary')
            mod = 'control';
        if (mod === 'win' || mod === 'meta')
            mod = 'super';
        mods.push(mod);
        rest = rest.slice(m[0].length);
    }
    mods.sort();
    return mods.map(x => `<${x}>`).join('') + rest.toLowerCase();
}

/** How MakeBorderless works for a client type: X11 windows get Motif hints, Wayland ones
 * (client-side decorations the compositor can't remove) are made fullscreen. */
export function borderlessMethod(clientType) {
    return clientType === 'x11' ? 'motif-hints' : 'fullscreen';
}
