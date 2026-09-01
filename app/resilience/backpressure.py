"""Backpressure and load shedding."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import BackpressureConfig

logger = logging.getLogger(__name__)


class BackpressureController:
    """
    Limits concurrent in-flight requests and bounds a wait queue.

    When the queue is full or wait times out → 503 (load shed).
    """

    def __init__(self, config: "BackpressureConfig") -> None:
        self.config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent_requests)
        self._queue_size = 0
        self._lock = asyncio.Lock()
        self.rejected_total = 0
        self.current_active = 0

    @property
    def queue_size(self) -> int:
        return self._queue_size

    async def acquire(self) -> bool:
        """
        Try to obtain a concurrency slot.
        Returns False if the request should be shed (503).
        """
        async with self._lock:
            if self._queue_size >= self.config.max_queue_size:
                self.rejected_total += 1
                return False
            self._queue_size += 1

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.config.queue_timeout,
            )
        except asyncio.TimeoutError:
            async with self._lock:
                self._queue_size -= 1
                self.rejected_total += 1
            return False

        async with self._lock:
            self._queue_size -= 1
            self.current_active += 1
        return True

    def release(self) -> None:
        self.current_active = max(0, self.current_active - 1)
        self._semaphore.release()
