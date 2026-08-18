"""Smoke tests for the draft engine prototype."""
from draft_engine.models import (
    DRAFT_ORDER_734, DRAFT_ORDER_740, TEAM_A, TEAM_B, build_state
)
from draft_engine.data import load_heroes, load_hero_stats, load_matchups
from draft_engine.scoring import DraftAdvisor


def _state():
    heroes = load_heroes()
    stats = load_hero_stats()
    matchups = load_matchups()
    return build_state(heroes, stats), matchups


def test_order_counts():
    for order in (DRAFT_ORDER_734, DRAFT_ORDER_740):
        assert len(order) == 24
        for team in (TEAM_A, TEAM_B):
            bans = sum(1 for t in order if t.team == team and t.action == "ban")
            picks = sum(1 for t in order if t.team == team and t.action == "pick")
            assert bans == 7, (order, team, bans)
            assert picks == 5, (order, team, picks)


def test_740_phase_one_is_current():
    expected = [t.team for t in DRAFT_ORDER_740[:7]]
    assert expected == ["a", "a", "b", "b", "a", "b", "b"]


def test_state_machine():
    state, matchups = _state()
    advisor = DraftAdvisor(state, matchups)
    assert len(state.legal_heroes()) >= 120
    while not state.done:
        turn = state.current_turn
        side = state.team_side(turn.team)
        suggestions = (advisor.suggest_bans(side, 3)
                       if turn.action == "ban"
                       else advisor.suggest_picks(side, 3))
        assert len(suggestions) == 3
        assert all(s.hero.id not in state.used_heroes for s in suggestions)
        state.apply_current(suggestions[0].hero.id, expected_action=turn.action)
    assert state.done
    assert len(state.radiant_picks) == 5
    assert len(state.dire_picks) == 5
    assert len(state.radiant_bans) == 7
    assert len(state.dire_bans) == 7


def test_role_model_respects_core_support_rule():
    from draft_engine.data import load_heroes
    from draft_engine.roles import load_hero_positions, role_kind
    heroes = {h["id"]: h["localized_name"] for h in load_heroes()}
    probs = load_hero_positions()
    by_name = {name: role_kind(probs[hid]) for hid, name in heroes.items()}
    assert by_name["Crystal Maiden"] == "support"
    assert by_name["Anti-Mage"] == "core"
    assert by_name["Windranger"] == "flex"
    assert by_name["Io"] == "flex"


def test_simulated_draft_ends_with_3_cores_and_2_supports():
    state, matchups = _state()
    advisor = DraftAdvisor(state, matchups)
    while not state.done:
        turn = state.current_turn
        side = state.team_side(turn.team)
        suggestion = (advisor.best_ban(side) if turn.action == "ban"
                      else advisor.best_pick(side))
        state.apply_current(suggestion.hero.id, expected_action=turn.action)
    for side in ("radiant", "dire"):
        picks = state.side_picks(side)
        assignment = advisor.lineup_assignment(picks)
        assert assignment is not None
        assert len(assignment) == 5
        cores = sum(1 for pos in assignment.values() if pos <= 3)
        supports = 5 - cores
        assert cores == 3, (side, cores)
        assert supports == 2, (side, supports)


def test_action_validation_and_undo():
    state, _ = _state()
    hid = state.legal_heroes()[0].id
    try:
        state.apply_current(hid, expected_action="pick")
    except ValueError:
        pass
    else:
        raise AssertionError("pick during ban phase should fail")
    assert state.index == 0
    state.apply_current(hid, expected_action="ban")
    assert state.index == 1
    state.undo()
    assert state.index == 0
    assert state.used_heroes == set()


if __name__ == "__main__":
    test_order_counts()
    test_740_phase_one_is_current()
    test_state_machine()
    test_role_model_respects_core_support_rule()
    test_simulated_draft_ends_with_3_cores_and_2_supports()
    test_action_validation_and_undo()
    print("all tests passed")
