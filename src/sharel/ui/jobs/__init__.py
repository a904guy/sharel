"""Every HotkeyType job, as ``job_<Name>`` methods mixed into the app."""
from __future__ import annotations

from .capture import CaptureJobs
from .other import OtherJobs
from .record import RecordJobs
from .tools import ToolJobs
from .upload import UploadJobs


class AllJobs(CaptureJobs, UploadJobs, RecordJobs, ToolJobs, OtherJobs):
    def init_jobs(self) -> None:
        self.last_picked_color = ""

    def shutdown_jobs(self) -> None:
        from ...capture.autocapture import AUTO
        AUTO.stop()
        try:
            from ...record.controller import CONTROLLER
            if CONTROLLER.active:
                CONTROLLER.stop()
        except ImportError:
            pass
        from ..windows.pin import close_all_pins
        close_all_pins()

    def busy_interactive(self) -> bool:
        """Something the user is still doing (region overlay, recording) keeps -autoclose waiting."""
        from ...record.controller import CONTROLLER
        return self.region_session is not None or CONTROLLER.active
