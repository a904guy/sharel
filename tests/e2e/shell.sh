#!/bin/bash
# Run an isolated, headless GNOME Shell for ShareL's end-to-end tests.
# Adapted from RootPilot's tests/e2e/nested-shell.sh (MIT, same author); every safety
# mechanism of the original is kept.
#
# It gets its own D-Bus session bus, its own dconf (XDG_CONFIG_HOME), its own
# extensions dir (XDG_DATA_HOME) with three extensions linked/enabled:
#   sharel@hawkins.app            from this repo
#   rootpilot@hawkins.app         from /home/mike/Projects/RootPilot (ROOTPILOT_REPO)
#   ubuntu-appindicators@ubuntu.com  the system copy (tray icon support)
# and its own XDG_RUNTIME_DIR. Nothing touches the real desktop session: apps
# launched with the printed environment open inside the headless shell instead.
# Its private dconf unbinds GNOME's own Print shortcuts so ShareL can grab Print
# (that never touches the real session's settings: see the private-bus guard).
#
# The private runtime dir matters: GNOME Shell writes a crash guard
# (gnome-shell-disable-extensions) there at startup, and a headless shell that
# died early must never leave it behind for the real session. Only PipeWire's
# sockets are shared, since ScreenCast frames flow through the user's daemon.
#
# Run one headless shell at a time: they register with logind/GDM for the
# user's session and two at once can knock each other over.
#
#   tests/e2e/shell.sh start DIR [OWNER_PID] -> starts it, writes DIR/env
#   tests/e2e/shell.sh stop DIR
#   source DIR/env                        -> point a shell at it
#
# A test shell must never outlive its test run (an orphaned one once grew to
# 64 GB of shared memory). So:
#   - a watchdog (DIR/watchdog.pid) stops the shell as soon as OWNER_PID dies
#     (default: whoever ran "start", e.g. pytest), even after a SIGKILL;
#   - the same watchdog SIGKILLs it if system-wide shared memory grows more than
#     SHAREL_TEST_SHMEM_MAX_GB (default 4) over its starting value, or available
#     memory drops under SHAREL_TEST_MIN_AVAIL_PCT (default 20) % of RAM: a
#     runaway shell can allocate shared memory that is charged outside its scope;
#   - the shell runs in its own systemd scope capped at SHAREL_TEST_MEMORY_MAX
#     (default 4G, no swap) when a systemd user manager is there;
#   - "stop" kills the shell's whole process group, escalating to SIGKILL.
set -euo pipefail

cmd=${1:?start|stop}
dir=$(realpath -m "${2:?state dir}")
repo=$(cd "$(dirname "$0")/../.." && pwd)
rootpilot=${ROOTPILOT_REPO:-/home/mike/Projects/RootPilot}
self=$(realpath "$0")  # the watchdog runs it later, from another cwd

# Field 22 of /proc/PID/stat: start time, so a recycled pid isn't mistaken for the owner.
starttime() { awk '{print $22}' "/proc/$1/stat" 2>/dev/null; }

case "$cmd" in
start)
    owner=${3:-$PPID}
    owner_start=$(starttime "$owner")
    [ -n "$owner_start" ] || { echo "shell.sh: owner pid $owner is not running" >&2; exit 2; }
    mkdir -p "$dir"/{cfg,data/gnome-shell/extensions,cache,pictures,videos,home-docs}
    ln -sfn "$repo/extension/sharel@hawkins.app" "$dir/data/gnome-shell/extensions/sharel@hawkins.app"
    if [ -d "$rootpilot/extension/rootpilot@hawkins.app" ]; then
        ln -sfn "$rootpilot/extension/rootpilot@hawkins.app" "$dir/data/gnome-shell/extensions/rootpilot@hawkins.app"
    fi
    # Pictures/Videos of the test session live in the state dir, never in the real home.
    cat > "$dir/cfg/user-dirs.dirs" <<DIRS
XDG_PICTURES_DIR="$dir/pictures"
XDG_VIDEOS_DIR="$dir/videos"
XDG_DOCUMENTS_DIR="$dir/home-docs"
DIRS
    # Unix socket paths are limited to 108 bytes, so the private runtime dir
    # lives under the real one (short path) but is its own directory.
    real_runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
    run=$(mktemp -d "$real_runtime/sharel-test-XXXXXX")
    echo "$run" > "$dir/runtime"
    for sock in pipewire-0 pipewire-0-manager; do
        if [ -e "$real_runtime/$sock" ]; then ln -sfn "$real_runtime/$sock" "$run/$sock"; fi
    done
    rm -f "$dir/bus.addr" "$dir/shell.pid" "$dir/shell.id" "$dir/scope"
    scope=()
    if [ -z "${SHAREL_TEST_NO_SCOPE:-}" ] && command -v systemd-run >/dev/null &&
        systemctl --user show-environment >/dev/null 2>&1; then
        unit="sharel-test-${run##*sharel-test-}.scope"
        echo "$unit" > "$dir/scope"
        # systemd-run needs the real runtime dir to reach the user manager; the shell gets the private one.
        scope=(env XDG_RUNTIME_DIR="$real_runtime" systemd-run --user --scope -q --unit="$unit"
               -p "MemoryMax=${SHAREL_TEST_MEMORY_MAX:-4G}" -p MemorySwapMax=0 -- env XDG_RUNTIME_DIR="$run")
    fi
    (
        cd "$dir"
        export XDG_CONFIG_HOME=$dir/cfg XDG_DATA_HOME=$dir/data XDG_CACHE_HOME=$dir/cache
        export XDG_RUNTIME_DIR=$run WAYLAND_DISPLAY=wayland-0
        unset DISPLAY XDG_SESSION_ID
        # The session script lives in a file: systemd-run would expand $VARS in its arguments
        # (turning $DBUS_SESSION_BUS_ADDRESS into the REAL session bus, $$ into "$").
        # The real bus address, for session.sh's guard (it never touches a setting on that bus).
        printf '%s\n' "${DBUS_SESSION_BUS_ADDRESS:-}" > "$dir/real-bus.addr"
        cat > "$dir/session.sh" <<'SESSION'
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ] || [ "$DBUS_SESSION_BUS_ADDRESS" = "$(cat real-bus.addr)" ] ||
    [ "$DBUS_SESSION_BUS_ADDRESS" = "unix:path=/run/user/$(id -u)/bus" ]; then
    echo "shell.sh: not inside a private D-Bus session; refusing to touch any setting" >&2
    exit 1
fi
echo "$DBUS_SESSION_BUS_ADDRESS" > bus.addr
gsettings set org.gnome.shell enabled-extensions \
    "['sharel@hawkins.app', 'rootpilot@hawkins.app', 'ubuntu-appindicators@ubuntu.com']"
gsettings set org.gnome.shell disable-user-extensions false
gsettings set org.gnome.desktop.interface toolkit-accessibility true
gsettings set org.gnome.desktop.a11y.applications screen-reader-enabled false
# Private dconf only: free Print for ShareL's accelerators, like a user who unbinds it.
gsettings set org.gnome.shell.keybindings screenshot "[]"
gsettings set org.gnome.shell.keybindings screenshot-window "[]"
gsettings set org.gnome.shell.keybindings show-screenshot-ui "[]"
gsettings set org.gnome.shell.keybindings show-screen-recording-ui "[]"
echo $$ > shell.pid
# RootPilot's extension: lets tests resume its kill switch over D-Bus (test sessions only).
export ROOTPILOT_TEST_ALLOW_DBUS_RESUME="${ROOTPILOT_TEST_DBUS_RESUME:-1}"
export SHAREL_TEST_SHELL=1
exec gnome-shell --headless --wayland --no-x11 --wayland-display "$WAYLAND_DISPLAY" \
    --virtual-monitor 1920x1080 --virtual-monitor 1600x900
SESSION
        "${scope[@]}" setsid dbus-run-session -- bash "$dir/session.sh" > "$dir/shell.log" 2>&1 < /dev/null &
    )
    for _ in $(seq 150); do
        [ -s "$dir/bus.addr" ] && grep -q "GNOME Shell started" "$dir/shell.log" 2>/dev/null && break
        sleep 0.1
    done
    # Identify the shell for "stop": pid + start time (+ its session id from our setsid).
    if [ -s "$dir/shell.pid" ]; then
        spid=$(cat "$dir/shell.pid")
        echo "$(starttime "$spid") $(ps -o sid= -p "$spid" | tr -d ' ')" > "$dir/shell.id"
    fi
    # The watchdog: its own session, so a Ctrl-C to the owner's process group doesn't take it down
    # before it has done its job. "stop" kills it when the run ends normally.
    setsid "$self" watch "$dir" "$owner" "$owner_start" >> "$dir/watchdog.log" 2>&1 < /dev/null &
    echo $! > "$dir/watchdog.pid"
    if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ "$(cat "$dir/bus.addr" 2>/dev/null)" = "$DBUS_SESSION_BUS_ADDRESS" ]; then
        echo "shell.sh: the test bus is the real session bus; refusing" >&2
        "$self" stop "$dir"
        exit 1
    fi
    if ! grep -q "GNOME Shell started" "$dir/shell.log"; then
        tail -20 "$dir/shell.log"
        "$self" stop "$dir"
        exit 1
    fi
    cat > "$dir/env" <<ENV
export DBUS_SESSION_BUS_ADDRESS='$(cat "$dir/bus.addr")'
export XDG_RUNTIME_DIR='$run' WAYLAND_DISPLAY='wayland-0'
export XDG_CONFIG_HOME='$dir/cfg' XDG_DATA_HOME='$dir/data' XDG_CACHE_HOME='$dir/cache'
export SHAREL_PICTURES_DIR='$dir/pictures' XDG_VIDEOS_DIR='$dir/videos' PULSE_SERVER='unix:$real_runtime/pulse/native'
export GDK_BACKEND=wayland ROOTPILOT_TEST_SHELL=1 SHAREL_TEST_SHELL=1 NO_AT_BRIDGE=0
unset DISPLAY XDG_SESSION_ID
ENV
    echo "$dir/env"
    ;;
stop)
    if [ -z "${SHAREL_FROM_WATCHDOG:-}" ] && [ -f "$dir/watchdog.pid" ]; then
        kill "$(cat "$dir/watchdog.pid")" 2>/dev/null || true
    fi
    rm -f "$dir/watchdog.pid"
    pid=$(cat "$dir/shell.pid" 2>/dev/null || true)
    want_start='' want_sid=''
    if [ -f "$dir/shell.id" ]; then read -r want_start want_sid < "$dir/shell.id" || true; fi
    # Signal only what is provably this test's shell: the recorded start time AND session id
    # still match. A stale pid file (pid recycled since) must never hit an unrelated process.
    if [ -n "$pid" ] && [ -n "$want_start" ] && [ -n "$want_sid" ] &&
        [ "$(starttime "$pid")" = "$want_start" ] &&
        [ "$(ps -o sid= -p "$pid" | tr -d ' ')" = "$want_sid" ]; then
        # The whole session our setsid made (dbus-run-session, its dbus-daemon, the shell, the apps
        # launched into it), by its sid as the process group; never init's, never our own group.
        target=$pid
        if [ "$want_sid" != 1 ] && [ "$want_sid" != "$(ps -o pgid= -p $$ | tr -d ' ')" ]; then
            target=-$want_sid
        fi
        kill -TERM -- "$target" 2>/dev/null || true
        for _ in $(seq 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
        if kill -0 "$pid" 2>/dev/null; then
            echo "shell.sh: shell $pid ignored SIGTERM for 5 s; sending SIGKILL" >&2
            kill -KILL -- "$target" 2>/dev/null || true
            for _ in $(seq 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
        fi
        kill -0 "$pid" 2>/dev/null && echo "shell.sh: shell $pid still running after SIGKILL" >&2
    fi
    # Gone, or not provably ours (then nothing was signalled): drop the identity files either way.
    rm -f "$dir/shell.pid" "$dir/shell.id"
    if [ -f "$dir/scope" ]; then
        # The scope's name is unique to this run, so killing it is safe: whatever is left goes.
        systemctl --user kill -s KILL "$(cat "$dir/scope")" 2>/dev/null || true
        rm -f "$dir/scope"
    fi
    if [ -f "$dir/runtime" ]; then
        run=$(cat "$dir/runtime")
        case "$run" in
        */sharel-test-*)
            # The test session's document portal ($run/doc) and gvfs ($run/gvfs) are FUSE mounts;
            # doc exposes REAL user files. Unmount them first, and only delete the directory when
            # nothing is mounted under it any more (and never cross into another filesystem).
            awk -v r="$run/" 'index($2, r) == 1 {print $2}' /proc/mounts | while IFS= read -r m; do
                fusermount3 -u "$m" 2>/dev/null || fusermount -u "$m" 2>/dev/null || true
            done
            if awk -v r="$run" '$2 == r || index($2, r "/") == 1 {found=1} END {exit !found}' /proc/mounts; then
                echo "shell.sh: mounts remain under $run; leaving it in place:" >&2
                awk -v r="$run" '$2 == r || index($2, r "/") == 1 {print "  " $2}' /proc/mounts >&2
            else
                rm -rf --one-file-system "$run"
                rm -f "$dir/runtime"
            fi
            ;;
        esac
    fi
    ;;
watch)
    # Internal (started by "start"): every 0.5 s, stop the shell if its owner is gone, or SIGKILL
    # it at once if system memory runs away (see the header).
    owner=${3:?}; owner_start=${4:?}
    kb() { awk -v k="$1:" '$1 == k {print $2}' /proc/meminfo; }
    shmem0=$(kb Shmem)
    shmem_max=$(( ${SHAREL_TEST_SHMEM_MAX_GB:-4} * 1024 * 1024 ))
    avail_min=$(( $(kb MemTotal) * ${SHAREL_TEST_MIN_AVAIL_PCT:-20} / 100 ))
    echo "$(date +%T) watching: owner $owner, Shmem baseline $((shmem0 / 1024)) MB," \
        "limits +$((shmem_max / 1024)) MB shared, $((avail_min / 1024)) MB available"
    while [ "$(starttime "$owner")" = "$owner_start" ]; do
        [ -f "$dir/shell.pid" ] || [ -f "$dir/runtime" ] || exit 0
        shmem=$(kb Shmem); avail=$(kb MemAvailable)
        if [ $((shmem - shmem0)) -gt "$shmem_max" ] || [ "$avail" -lt "$avail_min" ]; then
            echo "$(date +%T) MEMORY RUNAWAY: Shmem $((shmem / 1024)) MB (baseline $((shmem0 / 1024)) MB)," \
                "available $((avail / 1024)) MB: killing the test shell"
            pid=$(cat "$dir/shell.pid" 2>/dev/null || true)
            # Kill first, ask questions later: the identity check is in "stop".
            [ -f "$dir/scope" ] && systemctl --user kill -s KILL "$(cat "$dir/scope")" 2>/dev/null
            SHAREL_FROM_WATCHDOG=1 "$self" stop "$dir"
            [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo "shell $pid still alive after the kill" >&2
            exit 1
        fi
        sleep 0.5
    done
    echo "$(date +%T) owner $owner is gone: stopping the test shell"
    SHAREL_FROM_WATCHDOG=1 "$self" stop "$dir"
    ;;
*)
    echo "usage: $0 start|stop DIR" >&2
    exit 2
    ;;
esac
