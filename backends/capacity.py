"""Bounded origin-side FIFO admission and paced service starts."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capacity:
    enabled: bool = False
    rate: int = 40
    concurrency: int = 8
    queue: int = 16
    crash: bool = False
    cooldown: int = 10


class CapacityGate:
    def __init__(self) -> None:
        self.config = Capacity()
        self.active = 0
        self.waiting: deque[object] = deque()
        self.changed = asyncio.Condition()
        self.next_start = 0.0
        self.until = 0.0
        self.rejections: deque[float] = deque(maxlen=1000)
        self.arrivals: deque[float] = deque(maxlen=4000)
        self.starts: deque[float] = deque(maxlen=4000)
        self.accepted = self.rejected = self.failures = 0
        self.wait_ms = 0.0
        self.recovery_until = 0.0
        self.pressure_since = 0.0

    def failed(self) -> bool:
        now = time.monotonic()
        if self.until and now >= self.until:
            self.until = 0.0
            self.rejections.clear()
            self.pressure_since = 0.0
            self.recovery_until = now+2
        return self.until > now

    def reject(self, now: float) -> None:
        self.rejected += 1
        if self.rejections and now-self.rejections[-1]>.5 and len(self.waiting)<max(1,self.config.queue):
            self.pressure_since = 0.0
        self.rejections.append(now)
        while self.rejections and self.rejections[0] < now-2:
            self.rejections.popleft()
        self.check_pressure(now, True)

    def check_pressure(self, now: float, overflow: bool = False) -> None:
        pressure = overflow or (self.config.queue > 0 and len(self.waiting) >= self.config.queue)
        if not pressure:
            self.pressure_since = 0.0
            return
        if not self.pressure_since:
            self.pressure_since = now
        # Sustained full queue or repeated overflow; never a literal process crash.
        if (self.config.crash and not self.failed() and now >= self.recovery_until
                and now-self.pressure_since >= .75
                and (len(self.rejections) >= 3 or len(self.waiting) >= max(1,self.config.queue))):
            self.until = now+self.config.cooldown
            self.failures += 1
            self.changed.notify_all()

    async def configure(self, config: Capacity) -> None:
        async with self.changed:
            self.config = config
            self.next_start = 0.0
            self.until = self.recovery_until = self.pressure_since = 0.0
            self.rejections.clear()
            self.arrivals.clear()
            self.starts.clear()
            self.accepted = self.rejected = self.failures = 0
            self.wait_ms = 0.0
            self.changed.notify_all()

    async def recover(self) -> None:
        async with self.changed:
            self.until = self.recovery_until = self.pressure_since = 0.0
            self.rejections.clear()
            self.changed.notify_all()

    async def acquire(self) -> bool:
        began = time.monotonic()
        async with self.changed:
            self.arrivals.append(began)
            if not self.config.enabled:
                self.active += 1
                self.accepted += 1
                self.starts.append(began)
                return True
            if self.failed():
                self.rejected += 1
                return False
            def can_start(now: float) -> bool:
                return self.active < self.config.concurrency and now >= self.next_start
            if not self.waiting and can_start(began):
                self.next_start = began + 1/self.config.rate
                self.active += 1
                self.accepted += 1
                self.starts.append(began)
                return True
            if len(self.waiting) >= self.config.queue:
                self.reject(began)
                return False
            ticket = object()
            self.waiting.append(ticket)
            try:
                while True:
                    now = time.monotonic()
                    self.check_pressure(now)
                    if self.failed():
                        self.rejected += 1
                        return False
                    if not self.config.enabled or (self.waiting[0] is ticket and can_start(now)):
                        self.active += 1
                        self.accepted += 1
                        self.starts.append(now)
                        self.next_start = now + 1/self.config.rate
                        waited = (now-began)*1000
                        self.wait_ms = .2*waited+.8*self.wait_ms
                        return True
                    if now-began >= 2:
                        self.reject(now)
                        return False
                    try:
                        await asyncio.wait_for(self.changed.wait(),
                            timeout=.1 if self.active >= self.config.concurrency
                            else min(.1,max(.005,self.next_start-now)))
                    except TimeoutError:
                        pass
            finally:
                self.waiting.remove(ticket)
                self.changed.notify_all()

    async def release(self) -> None:
        async with self.changed:
            self.active -= 1
            if len(self.waiting)<self.config.queue:
                self.pressure_since = 0.0
            self.changed.notify_all()

    def snapshot(self) -> dict:
        now = time.monotonic()
        failed = self.failed()
        recent = sum(t >= now-1 for t in self.rejections)
        incoming = sum(t >= now-1 for t in self.arrivals)
        starts = sum(t >= now-1 for t in self.starts)
        state = ("failed" if failed else "recovering" if now < self.recovery_until
                 else "overloaded" if recent else "saturated" if self.waiting
                 else "healthy") if self.config.enabled else "unlimited"
        return {**asdict(self.config),"state":state,"active":self.active,
                "queued":len(self.waiting),"incoming_rps":incoming,"started_rps":starts,
                "utilization":round(100*starts/self.config.rate,1) if self.config.enabled else None,
                "accepted":self.accepted,"rejected":self.rejected,"failures":self.failures,
                "recent_rejections":recent,"queue_wait_ms":round(self.wait_ms,2),
                "remaining":round(max(0,self.until-now),1)}
