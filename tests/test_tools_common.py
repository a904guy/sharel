"""Shared tool plumbing: cancelable subprocesses and fonts."""
from __future__ import annotations

import threading
import time

import pytest

from sharel.tools._common import OperationCancelled, ToolError, find_font, load_font, run_cancelable


def test_run_collects_output_and_stdin():
    r = run_cancelable(["cat"], stdin=b"hello")
    assert r.returncode == 0 and r.stdout == b"hello"
    r = run_cancelable(["sh", "-c", "echo out; echo err >&2; exit 3"])
    assert (r.returncode, r.stdout, r.stderr) == (3, b"out\n", b"err\n")


def test_line_callback():
    lines = []
    run_cancelable(["printf", "a=1\\nb=2\\n"], on_stdout_line=lines.append)
    assert lines == ["a=1", "b=2"]


def test_cancel_kills_process():
    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()
    t0 = time.monotonic()
    with pytest.raises(OperationCancelled):
        run_cancelable(["sleep", "30"], ev)
    assert time.monotonic() - t0 < 5


def test_timeout_and_missing_binary():
    with pytest.raises(ToolError, match="timed out"):
        run_cancelable(["sleep", "30"], timeout=0.3)
    with pytest.raises(ToolError, match="not found"):
        run_cancelable(["/nonexistent/binary-xyz"])


def test_fonts():
    assert find_font() is None or find_font().endswith((".ttf", ".otf", ".ttc"))
    f = load_font(20)
    assert f.getbbox("Hg")[3] > 10
