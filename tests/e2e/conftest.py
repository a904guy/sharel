"""End-to-end fixtures: an isolated headless GNOME Shell (tests/e2e/shell.sh) with ShareL running in
it, driven through RootPilot (its stdio MCP client) the way a user would.

Run:  uv run pytest -m e2e tests/e2e          (one shell at a time; never touches the real desktop)
Evidence (RootPilot screenshots, ffprobe/OCR output) lands in tests/e2e/artifacts/<test name>/.
"""
from __future__ import annotations

import atexit
import contextlib
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ROOTPILOT = Path(os.environ.get("ROOTPILOT_REPO", "/home/mike/Projects/RootPilot"))
ARTIFACTS = HERE / "artifacts"

# The unit-test sandbox (tests/sandbox.py) replaced these for the whole pytest process; the e2e
# harness needs the real ones (PipeWire sockets, its private-bus guard).
SANDBOX_ONLY = ("GSETTINGS_BACKEND", "NO_AT_BRIDGE", "GDK_BACKEND", "SHAREL_UNIT_SANDBOX", "SHAREL_NO_KEYRING",
                "SHAREL_CONFIG_DIR", "SHAREL_DATA_DIR", "SHAREL_PICTURES_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME", "XDG_STATE_HOME", "SHAREL_NO_TRAY", "SHAREL_DISABLE_HOTKEYS",
                "SHAREL_NO_EXTENSION")


def real_env() -> dict[str, str]:
    env = dict(os.environ)
    for var in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY"):
        real = env.pop(f"SHAREL_REAL_{var}", None)
        if real:
            env[var] = real
        elif var == "WAYLAND_DISPLAY":
            env.pop(var, None)
    for var in SANDBOX_ONLY:
        env.pop(var, None)
    return env


def load_env_file(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            for part in shlex.split(line[len("export "):]):
                k, _, v = part.partition("=")
                env[k] = v
    return env


def _rootpilot_client():
    spec = importlib.util.spec_from_file_location("rootpilot_e2e_client", ROOTPILOT / "tests" / "e2e" / "client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Default after-capture tasks for the e2e session: no uploads unless a test sets a destination.
SEED_APP = {
    "default_task_settings": {
        "after_capture_job": ["CopyImageToClipboard", "SaveImageToFile"],
        "after_upload_job": ["CopyURLToClipboard"],
        "image_settings": {"image_auto_use_jpeg": False, "file_exist_action": "UniqueName"},
        "capture_settings": {"show_cursor": False},
        "general_settings": {"toast_window_duration": 30.0},
    },
    "show_tray": True,
    "upload_limit": 2,
}


CURRENT: list = []  # the running Session, for wait_for's diagnostics


def wait_for(fn, timeout=20.0, interval=0.25, what="condition"):
    """Poll ``fn`` until it returns something truthy; on timeout, show ShareL's recent log."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    tail = CURRENT[-1].log_tail(30) if CURRENT else ""
    raise TimeoutError(f"timed out waiting for {what}\n--- ShareL log ---\n{tail}")


class Session:
    """One headless shell + ShareL + helpers."""

    def __init__(self, state: Path, name: str) -> None:
        self.state = state
        self.name = name
        self.env_file = state / "env"
        self.env = {**real_env(), **load_env_file(self.env_file)}
        self.env.pop("DISPLAY", None)
        self.app_proc: subprocess.Popen | None = None
        self.artifacts = ARTIFACTS / name
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.cfg_dir = state / "cfg" / "ShareL"
        self.data_dir = state / "data" / "ShareL"
        self.pictures = state / "pictures"
        self.client_mod = _rootpilot_client()
        self.shots = 0

    # -- ShareL --

    def seed_config(self, app: dict | None = None, hotkeys: dict | None = None, uploaders: dict | None = None) -> None:
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        merged = json.loads(json.dumps(SEED_APP))
        if app:
            _deep_update(merged, app)
        (self.cfg_dir / "ApplicationConfig.json").write_text(json.dumps(merged, indent=2))
        if hotkeys is not None:
            (self.cfg_dir / "HotkeysConfig.json").write_text(json.dumps(hotkeys, indent=2))
        if uploaders is not None:
            (self.cfg_dir / "UploadersConfig.json").write_text(json.dumps(uploaders, indent=2))

    def start_app(self, *args: str, wait_s: float = 25.0) -> None:
        log = open(self.state / "sharel.log", "a")  # noqa: SIM115
        env = {**self.env, "SHAREL_DEV": "1", "SHAREL_EXECUTABLE": str(ROOT / ".venv" / "bin" / "sharel")}
        self.app_proc = subprocess.Popen([str(ROOT / ".venv" / "bin" / "sharel"), "-silent", *args], env=env,
                                         stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT), start_new_session=True)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            accels = self.ext("ListAccelerators")
            if accels and all(a["state"] == "grabbed" for a in accels):
                return
            time.sleep(0.3)
        raise RuntimeError(f"ShareL did not register its hotkeys: {self.ext('ListAccelerators')}\n{self.log_tail()}")

    def stop_app(self) -> None:
        if self.app_proc and self.app_proc.poll() is None:
            self.sharel("-ExitShareX")
            try:
                self.app_proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.app_proc.pid, signal.SIGTERM)
                self.app_proc.wait(5)
        self.app_proc = None

    def sharel(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
        """Run the ShareL CLI in the session (forwards to the running instance)."""
        return subprocess.run([str(ROOT / ".venv" / "bin" / "sharel"), *args], env=self.env, capture_output=True,
                              text=True, timeout=timeout, cwd=str(ROOT))

    def log_tail(self, n: int = 40) -> str:
        logs = sorted((self.data_dir / "Logs").glob("*.txt"))
        text = logs[-1].read_text() if logs else ""
        out = (self.state / "sharel.log").read_text() if (self.state / "sharel.log").exists() else ""
        lines = [ln for ln in (text + "\n" + out).splitlines()
                 if " PIL." not in ln and "Gtk-CRITICAL" not in ln and ln.strip()]
        return "\n".join(lines[-n:])

    def history(self) -> list[dict]:
        p = self.data_dir / "History.json"
        return json.loads(p.read_text()) if p.exists() else []

    def screenshots(self) -> list[Path]:
        root = self.pictures / "ShareL"
        return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []

    # -- D-Bus into the session --

    def gdbus(self, dest: str, path: str, method: str, *args: str, timeout: int = 30) -> str:
        out = subprocess.run(["gdbus", "call", "--session", "-d", dest, "-o", path, "-m", method, *args],
                             env=self.env, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            raise RuntimeError(f"{method}: {out.stderr.strip()}")
        return out.stdout.strip()

    def ext(self, method: str, *args: str):
        """Call the ShareL extension; JSON results come back parsed."""
        raw = self.gdbus("app.sharel.Shell", "/app/sharel/Shell", f"app.sharel.Shell.{method}", *args)
        import ast
        try:
            val = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
        if isinstance(val, tuple) and len(val) == 1:
            val = val[0]
        if isinstance(val, str):
            with contextlib.suppress(ValueError):
                return json.loads(val)
        return val

    def windows(self) -> list[dict]:
        return self.ext("GetWindows")

    def wait_window(self, title_part: str, timeout: float = 15.0, gone: bool = False) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hit = next((w for w in self.windows() if title_part.lower() in w["title"].lower()), None)
            if (hit is None) == gone:
                return hit
            time.sleep(0.2)
        raise TimeoutError(f"window {'still there' if gone else 'not found'}: {title_part!r}: "
                           f"{[w['title'] for w in self.windows()]}\n{self.log_tail()}")

    def focus_overlay(self, monitor: int = 0, timeout: float = 10.0) -> dict:
        """Give keyboard focus to the region-capture overlay on ``monitor`` (RootPilot's window list
        doesn't include the overlays, so its keyboard targeting can't do it) and wait until it has it."""
        ov = wait_for(lambda: next((w for w in self.windows() if "region capture" in w["title"]
                                    and w["monitor"] == monitor), None), timeout, what="region overlay")
        for _ in range(int(timeout / 0.1)):
            self.ext("Activate", f"uint64 {ov['id']}")
            time.sleep(0.1)
            cur = next((w for w in self.windows() if w["id"] == ov["id"]), None)
            if cur and cur["focus"]:
                time.sleep(0.3)  # the freshly mapped overlay is ready for input
                return cur
        raise TimeoutError("the region overlay never got keyboard focus")

    def clipboard(self, mime: str) -> bytes:
        """The compositor's clipboard selection for ``mime`` (read through the shell, as other apps see it)."""
        code = ("import sys, gi\n"
                "gi.require_version('Gio', '2.0')\n"
                "from gi.repository import Gio, GLib\n"
                "c = Gio.bus_get_sync(Gio.BusType.SESSION, None)\n"
                "r = c.call_sync('app.sharel.Shell', '/app/sharel/Shell', 'app.sharel.Shell', 'GetClipboard',"
                " GLib.Variant('(s)', (sys.argv[1],)), None, 0, 10000, None)\n"
                "sys.stdout.buffer.write(bytes(r.unpack()[0]))\n")
        out = subprocess.run(["/usr/bin/python3", "-c", code, mime], env=self.env, capture_output=True, timeout=30)
        return out.stdout if out.returncode == 0 else b""

    def tray_menu_click(self, *path: str) -> dict:
        """Activate a tray menu item by its label path (e.g. "Capture", "Capture entire screen") the way the
        shell does for a click: com.canonical.dbusmenu Event(id, "clicked") on ShareL's indicator menu."""
        code = r'''
import json, sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
c = Gio.bus_get_sync(Gio.BusType.SESSION, None)
items = c.call_sync("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.freedesktop.DBus.Properties",
    "Get", GLib.Variant("(ss)", ("org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems")), None, 0, 5000,
    None).unpack()[0]
names = c.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames", None,
    None, 0, 5000, None).unpack()[0]
candidates = []
for it in items:
    bus, _, path = it.partition("/")
    if bus and Gio.dbus_is_name(bus):
        candidates.append((bus, "/" + path if path else "/StatusNotifierItem"))
candidates += [(n, "/org/ayatana/NotificationItem/app_sharel_ShareL") for n in names if n.startswith(":")]
for bus, path in candidates:
    try:
        sid = c.call_sync(bus, path, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "Id")), None, 0, 3000, None).unpack()[0]
    except (GLib.Error, AttributeError):
        continue
    if sid != "app.sharel.ShareL":
        continue
    menu = c.call_sync(bus, path, "org.freedesktop.DBus.Properties", "Get",
        GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "Menu")), None, 0, 3000, None).unpack()[0]
    _rev, layout = c.call_sync(bus, menu, "com.canonical.dbusmenu", "GetLayout",
        GLib.Variant("(iias)", (0, -1, [])), None, 0, 5000, None).unpack()
    node = layout
    for want in sys.argv[1:]:
        kids = node[2]
        node = next((k for k in kids if k[1].get("label", "").replace("_", "") == want), None)
        if node is None:
            print(json.dumps({"ok": False, "missing": want, "have": [k[1].get("label") for k in kids]}))
            sys.exit(0)
    c.call_sync(bus, menu, "com.canonical.dbusmenu", "Event",
        GLib.Variant("(isvu)", (node[0], "clicked", GLib.Variant("s", ""), 0)), None, 0, 5000, None)
    print(json.dumps({"ok": True, "id": node[0], "toggle": node[1].get("toggle-state", -1)}))
    sys.exit(0)
print(json.dumps({"ok": False, "missing": "indicator"}))
'''
        out = subprocess.run(["/usr/bin/python3", "-c", code, *path], env=self.env, capture_output=True, text=True,
                             timeout=30)
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError(f"tray_menu_click failed: {out.stderr.strip()[-800:]}")
        return json.loads(out.stdout.strip())

    def launch(self, *argv: str) -> subprocess.Popen:
        return subprocess.Popen(list(argv), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)

    # -- RootPilot --

    def rootpilot(self):
        extra = {"XDG_STATE_HOME": str(self.state / "rp-state"), "XDG_VIDEOS_DIR": str(self.state / "videos"),
                 "ROOTPILOT_POLICY": str(self.state / "rp-policy.toml"), "GSETTINGS_BACKEND": "dconf",
                 "NO_AT_BRIDGE": "0"}
        policy = Path(extra["ROOTPILOT_POLICY"])
        if not policy.exists():
            policy.write_text('observe_only = false\n[apps]\ndeny = []\nallow = []\n[confirm]\ntools = []\n'
                              'keys = []\nfallback = "allow"\ntimeout = 10\n[rate]\nactions_per_minute = 2000\n'
                              '[commands]\nenabled = true\n[settings]\nwritable = []\n')
        Path(extra["XDG_STATE_HOME"]).mkdir(parents=True, exist_ok=True)
        # client.rootpilot() starts from os.environ (the unit sandbox's) and applies the env file;
        # extra_env fixes the two sandbox-only settings that would otherwise leak into the server.
        return self.client_mod.rootpilot(str(self.env_file), extra)

    def call(self, client, tag: str = ""):
        """A RootPilot caller whose screenshots go to artifacts/<module>/<tag or current test>/."""
        tag = tag or os.environ.get("PYTEST_CURRENT_TEST", "test").split("::")[-1].split(" ")[0]
        d = self.artifacts / tag
        d.mkdir(parents=True, exist_ok=True)
        return self.client_mod.Call(client, verbose=bool(os.environ.get("SHAREL_E2E_VERBOSE")), image_dir=str(d))


def _deep_update(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def _stop_shell(state: Path) -> None:
    subprocess.run([str(HERE / "shell.sh"), "stop", str(state)], timeout=60, env=real_env())


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def session(request, tmp_path_factory):
    """A fresh headless GNOME Shell with ShareL seeded (not yet started: tests call start_app)."""
    state = tmp_path_factory.mktemp("shell")
    out = subprocess.run([str(HERE / "shell.sh"), "start", str(state), str(os.getpid())], capture_output=True,
                         text=True, timeout=90, env=real_env())
    if out.returncode != 0:
        pytest.fail(f"cannot start the headless gnome-shell: {out.stdout[-800:]} {out.stderr[-800:]}")
    atexit.register(_stop_shell, state)
    name = Path(request.fspath).stem
    s = Session(state, name)
    CURRENT.append(s)
    s.seed_config()
    try:
        yield s
    finally:
        with contextlib.suppress(Exception):
            s.stop_app()
        _stop_shell(state)
        atexit.unregister(_stop_shell)


def found(data, name: str) -> bool:
    """A RootPilot find() result really contains an element called ``name`` (not just an echo of the query)."""
    items = (data or {}).get("data") or []
    return any(name in (it.get("name") or "") or name in (it.get("text") or "") for it in items if isinstance(it, dict))
