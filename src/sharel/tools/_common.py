"""Shared plumbing for the tool modules: cancellation, progress callbacks, cancelable subprocesses, fonts.

Conventions (the same ones ``sharel.core.tasks`` uses, so a UI can wire a task straight in):

* ``progress(done: int, total: int)`` — called with work done so far and the total (bytes, frames, files...).
* ``cancel: threading.Event | None`` — when set, the operation stops as soon as possible and raises
  :class:`OperationCancelled`.

API
---
``OperationCancelled``                 raised by any long operation that noticed ``cancel`` was set.
``check_cancel(cancel)``               raise ``OperationCancelled`` if ``cancel`` is set.
``run_cancelable(argv, cancel=None, stdin=None, timeout=None, env=None, on_stdout_line=None)``
                                       run a process, kill it on cancel/timeout; returns ``CompletedProcess``
                                       (bytes stdout/stderr). With ``on_stdout_line`` stdout is streamed line by
                                       line to the callback (as ``str``) instead of being collected.
``find_font(bold=False, mono=False)``  path of a system TrueType font (fontconfig), or ``None``.
``load_font(size, bold=False, mono=False)`` a Pillow font of that pixel size (falls back to Pillow's default).
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence

from PIL import ImageFont

ProgressFn = Callable[[int, int], None]


class OperationCancelled(Exception):
    """The user cancelled a long-running tool operation."""


class ToolError(RuntimeError):
    """An external tool failed (non-zero exit, missing binary, unreadable output)."""


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise OperationCancelled()


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolError(f"'{name}' was not found on PATH")
    return path


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_cancelable(argv: Sequence[str], cancel: threading.Event | None = None, *, stdin: bytes | None = None,
                   timeout: float | None = None, env: dict[str, str] | None = None,
                   on_stdout_line: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """Run ``argv`` and wait for it, killing the process when ``cancel`` is set (-> ``OperationCancelled``)
    or ``timeout`` seconds pass (-> ``ToolError``). The exit code is *not* checked; callers decide."""
    check_cancel(cancel)
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
    except FileNotFoundError as e:
        raise ToolError(f"'{argv[0]}' was not found") from e

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []

    def read_err() -> None:
        assert proc.stderr is not None
        for chunk in iter(lambda: proc.stderr.read(65536), b""):
            err_chunks.append(chunk)

    def read_out() -> None:
        assert proc.stdout is not None
        if on_stdout_line is None:
            for chunk in iter(lambda: proc.stdout.read(65536), b""):
                out_chunks.append(chunk)
        else:
            for raw in iter(proc.stdout.readline, b""):
                out_chunks.append(raw)
                try:
                    on_stdout_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
                except Exception:  # a misbehaving callback must not hang the reader
                    pass

    def write_in() -> None:
        assert proc.stdin is not None
        try:
            proc.stdin.write(stdin or b"")
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass

    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    if stdin is not None:
        threads.append(threading.Thread(target=write_in, daemon=True))
    for t in threads:
        t.start()

    waited = 0.0
    step = 0.05
    while proc.poll() is None:
        if cancel is not None:
            if cancel.wait(step):
                _kill(proc)
                for t in threads:
                    t.join(timeout=2)
                raise OperationCancelled()
        else:
            try:
                proc.wait(timeout=step)
            except subprocess.TimeoutExpired:
                pass
        waited += step
        if timeout is not None and waited >= timeout:
            _kill(proc)
            for t in threads:
                t.join(timeout=2)
            raise ToolError(f"'{argv[0]}' timed out after {timeout:.0f}s")
    for t in threads:
        t.join()
    return subprocess.CompletedProcess(list(argv), proc.returncode, b"".join(out_chunks), b"".join(err_chunks))


@functools.cache
def find_font(bold: bool = False, mono: bool = False) -> str | None:
    """Ask fontconfig for a sans (or monospace) TrueType font file."""
    pattern = ("monospace" if mono else "sans") + (":bold" if bold else "")
    fc = shutil.which("fc-match")
    if fc:
        try:
            out = subprocess.run([fc, "-f", "%{file}", pattern], capture_output=True, timeout=10, check=False)
            path = out.stdout.decode("utf-8", "replace").strip()
            if path and os.path.isfile(path):
                return path
        except (OSError, subprocess.TimeoutExpired):
            pass
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"):
        if os.path.isfile(candidate):
            return candidate
    return None


def load_font(size: float, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font(bold, mono)
    if path:
        try:
            return ImageFont.truetype(path, max(1, round(size)))
        except OSError:
            pass
    return ImageFont.load_default(size=max(1, round(size)))
