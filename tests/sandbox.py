"""A private D-Bus session and a headless Wayland display for the unit suite.

Imported by conftest.py before anything loads GTK, so no test can open a window on the human's
desktop, post a real notification, or talk to their GNOME Shell extension. Both processes die with
the test run.
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import tempfile
import time

_procs: list[subprocess.Popen] = []
_dirs: list[str] = []


def _cleanup() -> None:
    for p in _procs:
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()
    for d in _dirs:
        shutil.rmtree(d, ignore_errors=True)


def start() -> dict[str, str]:
    if os.environ.get("SHAREL_UNIT_SANDBOX") == "1":
        return {}
    env: dict[str, str] = {}
    # The e2e harness needs the real ones (PipeWire sockets, its private-bus guard).
    for var in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY"):
        if os.environ.get(var):
            env[f"SHAREL_REAL_{var}"] = os.environ[var]
    real_runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    base = real_runtime if os.path.isdir(real_runtime) and os.access(real_runtime, os.W_OK) else None
    run = tempfile.mkdtemp(prefix="sharel-unit-", dir=base)  # short path: Wayland sockets max 108 bytes
    os.chmod(run, 0o700)
    _dirs.append(run)
    atexit.register(_cleanup)
    # Private session bus.
    if shutil.which("dbus-daemon"):
        p = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        _procs.append(p)
        addr = p.stdout.readline().strip()
        if addr:
            env["DBUS_SESSION_BUS_ADDRESS"] = addr
    else:
        env["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
    # Headless display.
    if shutil.which("weston"):
        sock = "wl-unit"
        w = subprocess.Popen(["weston", "--backend=headless", f"--socket={sock}", "--width=1600", "--height=1000",
                              "--no-config", "--idle-time=0"],
                             env={**os.environ, "XDG_RUNTIME_DIR": run}, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        _procs.append(w)
        for _ in range(100):
            if os.path.exists(os.path.join(run, sock)):
                break
            time.sleep(0.05)
        env["WAYLAND_DISPLAY"] = sock
    env["XDG_RUNTIME_DIR"] = run
    # XDG config/data too: e.g. autostart.sync() must never touch the real ~/.config/autostart.
    home = tempfile.mkdtemp(prefix="sharel-unit-home-")
    _dirs.append(home)
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                     ("XDG_STATE_HOME", "state")):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
        env[var] = os.path.join(home, sub)
    env["GDK_BACKEND"] = "wayland"
    env["GSETTINGS_BACKEND"] = "memory"
    env["NO_AT_BRIDGE"] = "1"
    env["SHAREL_UNIT_SANDBOX"] = "1"
    os.environ.update(env)
    for var in ("DISPLAY", "XDG_SESSION_ID"):
        os.environ.pop(var, None)
    return env
