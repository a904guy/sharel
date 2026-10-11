"""The Debug log (ShareX's Debug > Show debug log): the recent log in memory, with listeners."""
from __future__ import annotations

import collections
import logging
import threading


class MemoryHandler(logging.Handler):
    def __init__(self, capacity: int = 5000) -> None:
        super().__init__(logging.DEBUG)
        self.records: collections.deque[str] = collections.deque(maxlen=capacity)
        self.listeners: list = []
        self._lock2 = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock2:
            self.records.append(line)
        for cb in list(self.listeners):
            try:
                cb(line)
            except Exception:
                pass

    def text(self) -> str:
        with self._lock2:
            return "\n".join(self.records)


MEMORY_HANDLER = MemoryHandler()
