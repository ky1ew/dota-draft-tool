from __future__ import annotations

import pytest

from draft_engine.roles import (
    best_assignment,
    can_finish_roster_shape,
    core_prob,
    role_kind,
    support_prob,
)


def test_core_support_flex_classification():
    core = [0.34, 0.33, 0.33, 0.0, 0.0]
    support = [0.0, 0.0, 0.0, 0.5, 0.5]
    flex = [0.25, 0.25, 0.0, 0.25, 0.25]

    assert role_kind(core) == "core"
    assert role_kind(support) == "support"
    assert role_kind(flex) == "flex"
    assert core_prob(core) > 0.99
    assert support_prob(support) > 0.99


def test_best_assignment_distinct_positions():
    positions = {
        1: [0.9, 0.0, 0.0, 0.0, 0.0],
        2: [0.0, 0.9, 0.0, 0.0, 0.0],
        3: [0.0, 0.0, 0.9, 0.0, 0.0],
        4: [0.0, 0.0, 0.0, 0.9, 0.0],
        5: [0.0, 0.0, 0.0, 0.0, 0.9],
    }
    result = best_assignment([1, 2, 3, 4, 5], positions)
    assert result is not None
    score, assignment = result
    assert score == pytest.approx(4.5)
    assert sorted(assignment.values()) == [1, 2, 3, 4, 5]


def test_best_assignment_infeasible():
    positions = {
        1: [0.9, 0.0, 0.0, 0.0, 0.0],
        2: [0.9, 0.0, 0.0, 0.0, 0.0],
    }
    assert best_assignment([1, 2], positions) is None


def test_roster_shape_feasibility():
    # 2 supports, 1 core and plenty of flex options in pool -> feasible.
    assert can_finish_roster_shape(
        core_only=1,
        support_only=2,
        flex=1,
        remaining_picks=1,
        pool_core_only=5,
        pool_support_only=5,
        pool_flex=5,
    )
    # Three support-only heroes can never form 3-core/2-support.
    assert not can_finish_roster_shape(
        core_only=0,
        support_only=3,
        flex=2,
        remaining_picks=0,
        pool_core_only=5,
        pool_support_only=5,
        pool_flex=5,
    )
    # Not enough core-capable heroes left in the pool.
    assert not can_finish_roster_shape(
        core_only=0,
        support_only=0,
        flex=1,
        remaining_picks=4,
        pool_core_only=0,
        pool_support_only=4,
        pool_flex=0,
    )
