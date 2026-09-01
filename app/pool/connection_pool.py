"""Per-backend HTTP connection pool with keep-alive reuse."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Optional

import aiohttp

from app.config import ConnectionPoolConfig, TimeoutsConfig

logger = logging.getLogger(__name__)


class PooledConnection:
    """Thin wrapper around an aiohttp connector-backed session usage."""

    def __init__(self, session: aiohttp.ClientSession, backend_id: str) -> None:
        self.session = session
        self.backend_id = backend_id
        self.created_at = time.monotonic()
        self.last_used = time.monotonic()
        self.in_use = False


class ConnectionPool:
    """
    Connection reuse per backend.

    Uses a shared aiohttp.ClientSession with TCPConnector limits.
    Tracks idle connections and enforces max per backend.
    """

    def __init__(
        self,
        pool_config: ConnectionPoolConfig,
        timeouts: TimeoutsConfig,
    ) -> None:
        self.pool_config = pool_config
        self.timeouts = timeouts
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._idle: dict[str, deque[PooledConnection]] = defaultdict(deque)
        self._active_count: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._connector = aiohttp.TCPConnector(
            limit=0,  # global unlimited; we enforce per-backend
            limit_per_host=self.pool_config.max_connections_per_backend,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            force_close=False,
            keepalive_timeout=self.pool_config.idle_timeout,
        )
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=self.timeouts.connect,
            sock_read=self.timeouts.read,
            sock_connect=self.timeouts.connect,
        )
        self._session = aiohttp.ClientSession(
            connector=self._connector,
            timeout=timeout,
            auto_decompress=True,
        )
        self._cleanup_task = asyncio.create_task(self._idle_cleanup())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        if self._connector:
            await self._connector.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("connection pool not started")
        return self._session

    async def acquire(self, backend_id: str) -> aiohttp.ClientSession:
        """Return the shared session; track active count for metrics."""
        async with self._lock:
            if self._active_count[backend_id] >= self.pool_config.max_connections_per_backend:
                # Still allow; connector will queue / reject. Caller may want to shed.
                pass
            self._active_count[backend_id] += 1
        return self.session

    async def release(self, backend_id: str) -> None:
        async with self._lock:
            self._active_count[backend_id] = max(0, self._active_count[backend_id] - 1)

    def active_connections(self, backend_id: str) -> int:
        return self._active_count.get(backend_id, 0)

    async def _idle_cleanup(self) -> None:
        while True:
            await asyncio.sleep(self.pool_config.idle_timeout / 2)
            # aiohttp connector handles most idle cleanup via keepalive_timeout
            # This loop is a placeholder for future explicit idle tracking.
