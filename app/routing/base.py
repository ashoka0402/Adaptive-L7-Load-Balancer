"""Base load-balancing strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.models.backend import Backend


class LoadBalancingStrategy(ABC):
    """Common interface for all routing algorithms."""

    @abstractmethod
    async def select_backend(self, backends: list[Backend]) -> Optional[Backend]:
        """Select a backend from the available list. Returns None if empty."""
        ...

    def name(self) -> str:
        return self.__class__.__name__
