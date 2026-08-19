from __future__ import annotations

from dataclasses import replace

import pytest

from draft_engine.lookahead import LookaheadEngine, static_evaluate
from draft_engine.scoring import DraftAdvisor


@pytest.fixture
def advisor(synthetic_state, synthetic_positions, neutral_matchups):
    return DraftAdvisor(
        synthetic_state,
        matchups=neutral_matchups,
        positions=synthetic_positions,
    )


def test_lookahead_does_not_mutate_state(advisor):
    original_index = advisor.state.index
    engine = LookaheadEngine(advisor)
    suggestions = engine.suggest("ban", "radiant", limit=5, depth=1)

    assert len(suggestions) == 5
    assert advisor.state.index == original_index
    assert all(s.hero.id not in advisor.state.used_heroes for s in suggestions)


def test_lookahead_pick_candidates_are_shape_valid(advisor):
    state = replace(advisor.state, index=7)  # first pick phase
    advisor.bind(state)
    engine = LookaheadEngine(advisor)
    suggestions = engine.suggest("pick", "radiant", limit=5, depth=1)

    assert len(suggestions) == 5
    for s in suggestions:
        assert advisor._candidate_shape_valid(s.hero, ())


def test_static_evaluate_is_zero_sum(advisor):
    assert static_evaluate(advisor, advisor.state, "radiant") == pytest.approx(
        -static_evaluate(advisor, advisor.state, "dire")
    )


def test_for_state_shares_expensive_data(advisor):
    clone = advisor.for_state(replace(advisor.state, index=1))
    assert clone is not advisor
    assert clone._matchup_lookup is advisor._matchup_lookup
    assert clone.positions is advisor.positions
    assert clone.synergy is advisor.synergy
    assert clone.state.index == 1
