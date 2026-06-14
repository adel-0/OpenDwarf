"""Map eval-scenario names to simulated starting worlds.

The eval harness names scenarios by a DF *save* (e.g. ``wolf-survival`` →
``save/wolf_encounter``).  For offline runs we instead seed an equivalent
``SimWorld``.  Only scenarios with a faithful in-sim model live here; the rest
remain live-DF-only until the simulator grows to cover them.
"""

from __future__ import annotations

from collections.abc import Callable

from opendwarf.sim.world import SimWorld

# scenario name → factory returning its starting SimWorld.
SIM_SCENARIOS: dict[str, Callable[[], SimWorld]] = {
    "wolf-survival": SimWorld.wolf_survival,
}


def has_sim(scenario_name: str) -> bool:
    """Whether *scenario_name* can be run against the offline simulator."""
    return scenario_name in SIM_SCENARIOS


def build_world(scenario_name: str) -> SimWorld:
    """Return a fresh starting ``SimWorld`` for *scenario_name*.

    Raises ``KeyError`` (with the list of supported scenarios) when there is no
    sim model — the caller should fall back to a live DF run.
    """
    try:
        factory = SIM_SCENARIOS[scenario_name]
    except KeyError:
        raise KeyError(
            f"no offline sim model for scenario {scenario_name!r}; "
            f"supported: {sorted(SIM_SCENARIOS)}"
        ) from None
    return factory()
