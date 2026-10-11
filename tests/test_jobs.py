"""Every HotkeyType has a job handler on the app, and tool jobs point at real modules."""
import importlib
import inspect

import pytest

from sharel.core.jobs import all_jobs, handler_name
from sharel.ui.jobs import AllJobs
from sharel.ui.jobs.tools import TOOL_WINDOWS


@pytest.mark.parametrize("job", all_jobs(), ids=lambda j: j.value)
def test_every_job_has_a_handler(job):
    fn = getattr(AllJobs, handler_name(job), None)
    assert callable(fn), job
    assert list(inspect.signature(fn).parameters)[:2] == ["self", "ctx"]


@pytest.mark.parametrize("job,module", sorted(TOOL_WINDOWS.items()))
def test_tool_window_modules_exist(job, module):
    mod = importlib.import_module(f"sharel.ui.tools.{module}")
    assert callable(getattr(mod, "open", None)), f"sharel.ui.tools.{module}.open missing"
