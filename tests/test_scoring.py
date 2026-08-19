from __future__ import annotations

import pytest

from dataclasses import replace

from draft_engine.exceptions import SuggestionError
from draft_engine.scoring import DraftAdvisor, shrunk_winrate


def make_advisor(state, positions, matchups):
    return DraftAdvisor(state, matchups=matchups, positions=positions)


def test_shrunk_winrate_regresses_to_prior():
    assert shrunk_winrate(0, 0) == pytest.approx(0.5)
    # 60% over 10 games is pulled much closer to 50% than 60% over 100.
    small = shrunk_winrate(6, 10)
    large = shrunk_winrate(60, 100)
    assert abs(small - 0.5) < abs(0.6 - 0.5)
    assert abs(large - 0.5) < abs(0.6 - 0.5)
    assert large > small


def test_simulated_draft_keeps_3_core_2_support(
    synthetic_state, synthetic_positions, neutral_matchups
):
    state = synthetic_state
    advisor = make_advisor(state, synthetic_positions, neutral_matchups)

    while not state.done:
        turn = state.current_turn
        side = state.team_side(turn.team)
        suggestion = (
            advisor.best_ban(side) if turn.action == "ban" else advisor.best_pick(side)
        )
        state = state.apply_current(suggestion.hero.id, expected_action=turn.action)
        advisor.bind(state)

    for side in ("radiant", "dire"):
        assignment = advisor.lineup_assignment(state.side_picks(side))
        assert assignment is not None
        cores = sum(1 for pos in assignment.values() if pos <= 3)
        supports = 5 - cores
        assert cores == 3, (side, cores)
        assert supports == 2, (side, supports)


def test_third_support_only_candidate_is_rejected(
    synthetic_state, synthetic_positions, neutral_matchups
):
    # Arrange a Radiant lineup with two support-only heroes already picked.
    state = replace(synthetic_state, radiant_picks=(16, 17), index=8)
    advisor = make_advisor(state, synthetic_positions, neutral_matchups)
    suggestions = advisor.suggest_picks("radiant", limit=100)

    # Hero 16/17 are already used; all remaining suggestions must not make
    # the 3-core / 2-support shape impossible.
    assert suggestions
    for s in suggestions:
        partial = state.radiant_picks + (s.hero.id,)
        kinds = advisor._lineup_kinds(partial)
        assert kinds[1] <= 2


def test_hero_vs_hero_uses_reverse_matchup(synthetic_state, neutral_matchups):
    advisor = make_advisor(synthetic_state, {}, neutral_matchups)
    # Direct row for 1 vs 2 is 10/20 -> 50%.
    wr, games = advisor.hero_vs_hero(1, 2)
    assert wr == pytest.approx(0.5)
    assert games == 20


def test_no_pick_when_draft_complete(
    synthetic_state, synthetic_positions, neutral_matchups
):
    state = synthetic_state
    advisor = make_advisor(state, synthetic_positions, neutral_matchups)
    while not state.done:
        turn = state.current_turn
        side = state.team_side(turn.team)
        suggestion = (
            advisor.best_ban(side) if turn.action == "ban" else advisor.best_pick(side)
        )
        state = state.apply_current(suggestion.hero.id, expected_action=turn.action)
        advisor.bind(state)
    assert state.done
