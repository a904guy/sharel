"""A private D-Bus session and a headless Wayland display for the unit suite.

Imported by conftest.py before anything loads GTK, so no test can open a window on the human's
desktop, post a real notification, or talk to their GNOME Shell extension. Both processes die with
the test run, even when it is SIGKILLed or times out: they are started with PR_SET_PDEATHSIG, and a
detached watchdog removes the sandbox's directories once the test process is gone (atexit does it on a
normal exit).

The bus has its own config: one private, empty service directory and none of the standard session
service directories. Otherwise calling a well-known name (org.freedesktop.FileManager1, the
notification daemon, portals) would D-Bus-activate the real service. The daemon also starts with the
sandboxed environment, so even a service a test installs in ``SHAREL_UNIT_DBUS_SERVICES`` runs on
the headless display with the private XDG directories.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import shutil
import signal
import subprocess
import tempfile
import time

_procs: list[subprocess.Popen] = []
_dirs: list[str] = []

_PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:
    """preexec_fn: the kernel SIGKILLs the child when the test process exits, however it exits."""
    ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if os.getppid() == 1:  # the parent already died between fork and prctl
        os._exit(1)


_WATCHDOG = """
import os, shutil, sys, time
pid = int(sys.argv[1])
while True:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        break
    except PermissionError:
        pass
    time.sleep(0.3)
time.sleep(0.5)  # let the kernel finish the pdeath kills, so nothing recreates files
for d in sys.argv[2:]:
    shutil.rmtree(d, ignore_errors=True)
"""


def _start_watchdog(dirs: list[str]) -> None:
    """Removes ``dirs`` when this process is gone (SIGKILL included: atexit doesn't run then)."""
    import sys
    subprocess.Popen([sys.executable, "-c", _WATCHDOG, str(os.getpid()), *dirs], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


_BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <keep_umask/>
  <listen>unix:tmpdir={run}</listen>
  <auth>EXTERNAL</auth>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


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
    # Headless display.
    if shutil.which("weston"):
        sock = "wl-unit"
        w = subprocess.Popen(["weston", "--backend=headless", f"--socket={sock}", "--width=1600", "--height=1000",
                              "--no-config", "--idle-time=0"],
                             env={**os.environ, "XDG_RUNTIME_DIR": run}, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent)
        _procs.append(w)
        for _ in range(100):
            if os.path.exists(os.path.join(run, sock)):
                break
            time.sleep(0.05)
        env["WAYLAND_DISPLAY"] = sock
    else:
        os.environ.pop("WAYLAND_DISPLAY", None)  # no headless display: never fall back to the real one
    env["XDG_RUNTIME_DIR"] = run
    # XDG config/data too: e.g. autostart.sync() must never touch the real ~/.config/autostart.
    home = tempfile.mkdtemp(prefix="sharel-unit-home-")
    _dirs.append(home)
    _start_watchdog([run, home])
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                     ("XDG_STATE_HOME", "state")):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
        env[var] = os.path.join(home, sub)
    env["GDK_BACKEND"] = "wayland"
    env["GSETTINGS_BACKEND"] = "memory"
    env["NO_AT_BRIDGE"] = "1"
    env["SHAREL_UNIT_SANDBOX"] = "1"
    # Private session bus, started last: with the sandboxed environment and nothing activatable.
    if shutil.which("dbus-daemon"):
        services = os.path.join(run, "dbus-services")
        os.makedirs(services)
        conf = os.path.join(run, "session.conf")
        with open(conf, "w") as fh:
            fh.write(_BUS_CONFIG.format(run=run, services=services))
        daemon_env = {k: v for k, v in os.environ.items()
                      if k not in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_ID")}
        daemon_env.update(env)
        p = subprocess.Popen(["dbus-daemon", f"--config-file={conf}", "--nofork", "--print-address=1"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=daemon_env,
                             preexec_fn=_die_with_parent)
        _procs.append(p)
        addr = p.stdout.readline().strip()
        env["DBUS_SESSION_BUS_ADDRESS"] = addr or "disabled:"
        env["SHAREL_UNIT_DBUS_SERVICES"] = services
    else:
        env["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
    os.environ.update(env)
    for var in ("DISPLAY", "XDG_SESSION_ID"):
        os.environ.pop(var, None)
    return env
