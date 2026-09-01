"""Controlled retry policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import RetryConfig
    from app.models.request import RequestContext


class RetryPolicy:
    """
    Safe retries: only idempotent methods by default, limited attempts,
    only on retryable status codes or connection-level failures.
    """

    def __init__(self, config: "RetryConfig") -> None:
        self.config = config

    def should_retry(
        self,
        ctx: "RequestContext",
        *,
        status_code: int | None = None,
        is_connection_error: bool = False,
    ) -> bool:
        if not self.config.enabled:
            return False
        if ctx.retry_count >= self.config.max_attempts - 1:
            return False
        if ctx.method not in self.config.retryable_methods:
            return False
        if is_connection_error:
            return True
        if status_code is not None and status_code in self.config.retryable_status_codes:
            return True
        return False
