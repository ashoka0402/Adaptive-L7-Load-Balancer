"""Configurable failure modes for test backends."""

from __future__ import annotations

import asyncio
import random
from enum import Enum


class FailureMode(str, Enum):
    NORMAL = "normal"
    SLOW = "slow"
    RANDOM_LATENCY = "random_latency"
    ERROR = "error"
    TIMEOUT = "timeout"
    OFFLINE = "offline"


class FailureInjector:
    def __init__(
        self,
        mode: FailureMode = FailureMode.NORMAL,
        slow_ms: int = 100,
        error_rate: float = 1.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.mode = mode
        self.slow_ms = slow_ms
        self.error_rate = error_rate
        self.timeout_seconds = timeout_seconds

    async def maybe_inject(self) -> tuple[bool, int | None]:
        """
        Returns (should_error, status_code_or_None).
        If should_error is True and status is None → hang (timeout).
        """
        if self.mode == FailureMode.OFFLINE:
            # Caller should not start the server; treated as connection refused
            return True, None

        if self.mode == FailureMode.TIMEOUT:
            await asyncio.sleep(self.timeout_seconds)
            return True, None

        if self.mode == FailureMode.ERROR:
            if random.random() < self.error_rate:
                return True, 500
            return False, None

        if self.mode == FailureMode.SLOW:
            await asyncio.sleep(self.slow_ms / 1000.0)
            return False, None

        if self.mode == FailureMode.RANDOM_LATENCY:
            delay = random.uniform(0.01, self.slow_ms / 1000.0)
            await asyncio.sleep(delay)
            return False, None

        # NORMAL
        return False, None
