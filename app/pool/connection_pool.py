"""Per-backend HTTP connection pool with keep-alive reuse."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import aiohttp

from app.config import ConnectionPoolConfig, TimeoutsConfig

logger = logging.getLogger(__name__)


class ConnectionPool:
    """
    Connection reuse per backend.

    Uses a shared aiohttp.ClientSession with TCPConnector limits.
    The connector owns idle reuse and enforces total and per-host connection limits.
    """

    def __init__(
        self,
        pool_config: ConnectionPoolConfig,
        timeouts: TimeoutsConfig,
    ) -> None:
        self.pool_config = pool_config
        self.timeouts = timeouts
        self._connector: aiohttp.TCPConnector | None = None
        self._session: aiohttp.ClientSession | None = None
        self._active_count: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._connector = aiohttp.TCPConnector(
            limit=self.pool_config.max_total_connections,
            limit_per_host=self.pool_config.max_connections_per_backend,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            force_close=False,
            keepalive_timeout=self.pool_config.idle_timeout,
        )
        timeout = aiohttp.ClientTimeout(
            total=self.timeouts.connect + self.timeouts.read + self.timeouts.write,
            connect=self.timeouts.connect,
            sock_read=self.timeouts.read,
            sock_connect=self.timeouts.connect,
        )
        self._session = aiohttp.ClientSession(
            connector=self._connector,
            timeout=timeout,
            auto_decompress=False,  # preserve Content-Encoding and bytes verbatim
            cookie_jar=aiohttp.DummyCookieJar(),  # never share upstream cookies across clients
        )

    async def stop(self) -> None:
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
            self._active_count[backend_id] += 1
        return self.session

    async def release(self, backend_id: str) -> None:
        async with self._lock:
            count = self._active_count.get(backend_id, 0)
            if count <= 1:
                self._active_count.pop(backend_id, None)
            else:
                self._active_count[backend_id] = count - 1

    def active_connections(self, backend_id: str) -> int:
        return self._active_count.get(backend_id, 0)
