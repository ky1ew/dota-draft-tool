from __future__ import annotations

import pytest

from draft_engine.models import (
    DRAFT_ORDER_740,
    DraftState,
    Hero,
    HeroStats,
)


@pytest.fixture
def synthetic_state() -> DraftState:
    """A small, fully offline draft state for fast unit tests."""
    heroes: dict[int, Hero] = {}
    stats: dict[int, HeroStats] = {}
    roles = (
        [("Carry", "Nuker")] * 15
        + [("Support", "Disabler")] * 8
        + [("Carry", "Support")] * 7
    )
    for hid in range(1, 31):
        heroes[hid] = Hero(
            id=hid,
            name=f"hero_{hid}",
            localized_name=f"Hero {hid}",
            primary_attr="str",
            attack_type="Melee",
            roles=roles[hid - 1],
            cm_enabled=True,
        )
        stats[hid] = HeroStats(
            id=hid,
            localized_name=f"Hero {hid}",
            cm_enabled=True,
            pub_pick=100,
            pub_win=50,
            pro_pick=5,
            pro_win=2,
            pro_ban=3,
        )
    return DraftState(heroes=heroes, hero_stats=stats, order=DRAFT_ORDER_740)


@pytest.fixture
def synthetic_positions() -> dict[int, list[float]]:
    """1-15 are cores, 16-23 supports, 24-30 flex."""
    positions: dict[int, list[float]] = {}
    for hid in range(1, 16):
        positions[hid] = [0.34, 0.33, 0.33, 0.0, 0.0]
    for hid in range(16, 24):
        positions[hid] = [0.0, 0.0, 0.0, 0.5, 0.5]
    for hid in range(24, 31):
        positions[hid] = [0.25, 0.25, 0.0, 0.25, 0.25]
    return positions


@pytest.fixture
def neutral_matchups() -> dict[int, dict[int, dict[str, int]]]:
    matrix: dict[int, dict[int, dict[str, int]]] = {}
    for hid in range(1, 31):
        matrix[hid] = {
            opp: {"wins": 10, "games": 20} for opp in range(1, 31) if opp != hid
        }
    return matrix
