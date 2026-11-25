import pytest

from app.experiments import RoutingSimulation


@pytest.mark.asyncio
async def test_simulation_is_reproducible_and_exercises_all_backends():
    simulation = RoutingSimulation()

    first = await simulation.run("adaptive", seed=11)
    second = await simulation.run("adaptive", seed=11)

    assert first == second
    assert first.requests == 1500
    assert set(first.selections) == {"b1", "b2", "b3"}
    assert sum(first.selections.values()) == first.requests
