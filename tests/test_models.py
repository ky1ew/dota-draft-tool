from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from draft_engine.exceptions import InvalidMoveError
from draft_engine.models import (
    DRAFT_ORDER_734,
    DRAFT_ORDER_740,
    TEAM_A,
    TEAM_B,
    Hero,
)


@pytest.mark.parametrize("order", [DRAFT_ORDER_734, DRAFT_ORDER_740])
def test_draft_order_totals(order):
    assert len(order) == 24
    for team in (TEAM_A, TEAM_B):
        bans = sum(1 for t in order if t.team == team and t.action == "ban")
        picks = sum(1 for t in order if t.team == team and t.action == "pick")
        assert bans == 7
        assert picks == 5


def test_740_first_phase_order():
    assert [t.team for t in DRAFT_ORDER_740[:7]] == ["a", "a", "b", "b", "a", "b", "b"]


def test_apply_current_returns_new_state(synthetic_state):
    hero = synthetic_state.legal_heroes()[0]
    original = synthetic_state
    updated = synthetic_state.apply_current(hero.id, expected_action="ban")

    assert updated is not original
    assert original.index == 0
    assert original.used_heroes == set()
    assert updated.index == 1
    assert hero.id in updated.radiant_bans


def test_action_validation(synthetic_state):
    hero = synthetic_state.legal_heroes()[0]
    with pytest.raises(InvalidMoveError):
        synthetic_state.apply_current(hero.id, expected_action="pick")


def test_undo_returns_previous_state(synthetic_state):
    hero = synthetic_state.legal_heroes()[0]
    updated = synthetic_state.apply_current(hero.id, expected_action="ban")
    undone = updated.undo()

    assert undone is not updated
    assert undone.index == 0
    assert undone.used_heroes == set()
    # Original untouched.
    assert updated.index == 1


def test_reset_returns_clean_state(synthetic_state):
    hero = synthetic_state.legal_heroes()[0]
    updated = synthetic_state.apply_current(hero.id, expected_action="ban")
    reset = updated.reset()
    assert reset.index == 0
    assert reset.used_heroes == set()


def test_hero_is_frozen_and_hashable():
    hero = Hero(
        id=1,
        name="npc_hero",
        localized_name="Anti-Mage",
        primary_attr="agi",
        attack_type="Melee",
        roles=("Carry",),
    )
    assert {hero: "ok"}[hero] == "ok"
    with pytest.raises(FrozenInstanceError):
        hero.localized_name = "Changed"
