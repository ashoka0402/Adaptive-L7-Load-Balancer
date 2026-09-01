"""Request context models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RequestContext:
    """Per-request context carried through the proxy path."""

    request_id: str
    method: str
    path: str
    query_string: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    start_time: float = field(default_factory=time.monotonic)
    backend_id: Optional[str] = None
    retry_count: int = 0
    selected_latency: float = 0.0

    @classmethod
    def create(
        cls,
        method: str,
        path: str,
        query_string: str = "",
        headers: Optional[dict[str, str]] = None,
    ) -> "RequestContext":
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        rid = hdrs.get("x-request-id") or str(uuid.uuid4())
        return cls(
            request_id=rid,
            method=method.upper(),
            path=path,
            query_string=query_string,
            headers=hdrs,
        )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time
