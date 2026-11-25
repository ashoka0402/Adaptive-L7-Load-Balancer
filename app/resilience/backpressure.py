"""Bounded, cancellation-safe admission on a single asyncio event loop."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import BackpressureConfig


class BackpressureController:
    def __init__(self, config: BackpressureConfig) -> None:
        self.config = config
        self._semaphore = asyncio.BoundedSemaphore(config.max_concurrent_requests)
        self._queue_size = 0
        self.rejected_total = 0
        self.current_active = 0

    @property
    def queue_size(self) -> int:
        return self._queue_size

    async def acquire(self) -> bool:
        # No await between availability check and immediate acquisition. Semaphore
        # honors existing waiters; a zero-length queue still permits idle slots.
        if not self._semaphore.locked():
            await self._semaphore.acquire()
            self.current_active += 1
            return True
        if self._queue_size >= self.config.max_queue_size:
            self.rejected_total += 1
            return False
        self._queue_size += 1
        try:
            async with asyncio.timeout(self.config.queue_timeout):
                await self._semaphore.acquire()
            self.current_active += 1
            return True
        except TimeoutError:
            self.rejected_total += 1
            return False
        finally:
            self._queue_size -= 1

    def release(self) -> None:
        if self.current_active <= 0:
            raise RuntimeError("release without acquired admission slot")
        self.current_active -= 1
        self._semaphore.release()
