"""Integration tests for the reverse proxy (require running backends)."""

import pytest

# These are markers for manual / docker-compose based integration.
# Full end-to-end is exercised via docker compose + locust.


@pytest.mark.asyncio
async def test_placeholder():
    """Placeholder — run against live stack with: docker compose up."""
    assert True
