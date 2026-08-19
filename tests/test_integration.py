"""Optional integration tests that use the local OpenDota cache.

Skipped automatically on a fresh clone where cache/ has not been built.
"""

from __future__ import annotations

import pytest

from draft_engine.data import CACHE_DIR


@pytest.mark.skipif(
    not (CACHE_DIR / "heroes.json").exists(),
    reason="OpenDota cache not built; run the demo first",
)
def test_real_data_draft_has_valid_lineup_shape():
    from draft_engine.data import load_heroes, load_hero_stats, load_matchups
    from draft_engine.models import build_state
    from draft_engine.scoring import DraftAdvisor

    state = build_state(load_heroes(), load_hero_stats())
    advisor = DraftAdvisor(state, load_matchups())
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
        assert cores == 3
