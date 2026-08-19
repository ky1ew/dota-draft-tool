"""Beam-search lookahead on top of the immutable draft state.

The greedy scorer answers "what is the best move right now?".  Lookahead
additionally asks "where does this move leave us two or four turns from
now?".

Algorithm
---------
* Root candidates: the top ``root_beam`` moves from the greedy scorer.
* For every root candidate we run a minimax-style beam search:
  - expand each surviving state with the top ``beam_width`` greedy moves
    for the side whose turn it is,
  - evaluate children with a static evaluator from the perspective of the
    original side,
  - when it is **our** turn keep the best children, when it is the
    **enemy's** turn keep the worst (for us).
* Final score = ``immediate_weight * greedy_score +
  (1 - immediate_weight) * lookahead_value``.

This is intentionally transparent: suggestions still carry the greedy
reasons plus a "lookahead eval" line.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import DEFAULT_CONFIG, SearchConfig
from .models import Action, DraftState, Side, Turn
from .scoring import DraftAdvisor, Suggestion

logger = logging.getLogger(__name__)


def static_evaluate(advisor: DraftAdvisor, state: DraftState, side: Side) -> float:
    """Heuristic advantage for `side`, scaled like greedy scores (0-50)."""
    my_picks = state.side_picks(side)
    enemy_side: Side = "dire" if side == "radiant" else "radiant"
    enemy_picks = state.side_picks(enemy_side)
    score = 0.0

    # Lineup shape quality and meta strength for each revealed lineup.
    for picks, sign in ((my_picks, 1.0), (enemy_picks, -1.0)):
        if not picks:
            continue
        quality = advisor.lineup_quality(picks)
        if quality is not None:
            score += sign * quality * 15.0
        metas = [advisor.meta_score(advisor.state.hero_stats[hid])[0] for hid in picks]
        score += sign * (sum(metas) / len(metas)) * 0.2

    # Average matchup edge across every revealed pair.
    if my_picks and enemy_picks:
        edges = []
        for mine in my_picks:
            for theirs in enemy_picks:
                wr, _ = advisor.hero_vs_hero(mine, theirs)
                edges.append(wr - 0.5)
        if edges:
            score += (sum(edges) / len(edges)) * 40.0

    # Average same-team synergy edge (only when synergy data is present).
    if advisor.synergy:
        for picks, sign in ((my_picks, 1.0), (enemy_picks, -1.0)):
            if len(picks) < 2:
                continue
            edges = []
            for i, a in enumerate(picks):
                for b in picks[i + 1 :]:
                    wr, _ = advisor.hero_with_hero(a, b)
                    edges.append(wr - 0.5)
            if edges:
                score += sign * (sum(edges) / len(edges)) * 50.0

    return score


@dataclass(frozen=True)
class _BeamNode:
    state: DraftState
    value: float


class LookaheadEngine:
    def __init__(self, advisor: DraftAdvisor, config: SearchConfig | None = None):
        self.advisor = advisor
        self.config = config or DEFAULT_CONFIG.search

    # ------------------------------------------------------------------
    def _moves(self, state: DraftState, turn: Turn) -> list[Suggestion]:
        advisor = self.advisor.for_state(state)
        side = state.team_side(turn.team)
        if turn.action == "pick":
            return advisor.suggest_picks(side, limit=self.config.beam_width)
        return advisor.suggest_bans(side, limit=self.config.beam_width)

    def _evaluate(self, state: DraftState, side: Side) -> float:
        return static_evaluate(self.advisor.for_state(state), state, side)

    def _beam_search(self, root: DraftState, depth: int, side: Side) -> float:
        """Minimax beam search starting from an already-applied root move."""
        beams = [_BeamNode(root, self._evaluate(root, side))]
        for _ in range(max(0, depth)):
            if not beams:
                break

            first_turn = beams[0].state.current_turn
            if first_turn is None:
                break
            acting: Side = beams[0].state.team_side(first_turn.team)

            expanded: list[_BeamNode] = []
            for beam in beams:
                turn = beam.state.current_turn
                if turn is None:
                    continue
                for move in self._moves(beam.state, turn):
                    child = beam.state.apply_current(
                        move.hero.id, expected_action=turn.action
                    )
                    expanded.append(_BeamNode(child, self._evaluate(child, side)))

            if not expanded:
                break

            # Our turn: keep the best outcomes. Enemy turn: keep the worst.
            keep_best = acting == side
            expanded.sort(key=lambda node: node.value, reverse=keep_best)
            beams = expanded[: self.config.beam_width]

        if not beams:
            return self._evaluate(root, side)
        values = [node.value for node in beams]
        return sum(values) / len(values)

    def suggest(
        self, action: Action, side: Side, limit: int = 5, depth: int | None = None
    ) -> list[Suggestion]:
        state = self.advisor.state
        depth = self.config.max_depth if depth is None else depth

        if action == "pick":
            roots = self.advisor.suggest_picks(side, limit=self.config.root_beam)
        else:
            roots = self.advisor.suggest_bans(side, limit=self.config.root_beam)

        if len(roots) <= 1:
            return roots[:limit]

        results: list[Suggestion] = []
        for root in roots:
            child = state.apply_current(root.hero.id, expected_action=action)
            value = self._beam_search(child, max(0, depth - 1), side)
            combined = (
                self.config.immediate_weight * root.score
                + (1.0 - self.config.immediate_weight) * value
            )
            reasons = root.reasons + (f"lookahead eval {value:+.1f}",)
            components = dict(root.components)
            components["greedy"] = round(self.config.immediate_weight * root.score, 2)
            components["lookahead"] = round(
                (1.0 - self.config.immediate_weight) * value, 2
            )
            results.append(
                Suggestion(root.hero, round(combined, 2), reasons[:4], components)
            )

        results.sort(key=lambda s: (-s.score,))
        return results[:limit]
