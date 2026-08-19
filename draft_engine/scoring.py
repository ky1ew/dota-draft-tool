"""Draft suggestion engine.

This is a deliberately transparent, explainable scorer (not a black-box
model).  It combines:

  * pairwise counter data from OpenDota /heroes/{id}/matchups
  * current-patch meta data from /heroStats
  * empirical position (1-5) data inferred from parsed matches
  * rough role-balance checks (initiation, disable, waveclear, durable...)

Dota lineups must contain **3 core heroes (positions 1-3) and 2 support
heroes (positions 4-5)**.  The position model therefore:

  * classifies every hero as core-only / support-only / flex,
  * rejects candidates that make a 3-core + 2-support shape impossible,
  * scores picks by how much they improve the best position assignment.

Every score contribution is attached to a human-readable reason so the
captain can decide whether they agree with the machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .config import DEFAULT_CONFIG, EngineConfig, ScoringWeights
from .exceptions import SuggestionError
from .models import Action, DraftState, Hero, HeroStats, Side
from .roles import (
    best_assignment,
    can_finish_roster_shape,
    load_hero_positions,
    position_entropy,
    role_kind,
)

logger = logging.getLogger(__name__)

# Non-core/support role tags we still want in a well-rounded draft.
ROLE_MINIMUMS: dict[str, int] = {
    "Initiator": 1,
    "Disabler": 2,
    "Nuker": 2,
    "Durable": 1,
    "Pusher": 1,
    "Escape": 0,
}


def shrunk_winrate(wins: int, games: int, cfg: EngineConfig = DEFAULT_CONFIG) -> float:
    prior = 0.5
    pseudo = cfg.matchup.pseudo_count
    if games <= 0:
        return prior
    return (wins + pseudo * prior) / (games + pseudo)


@dataclass(frozen=True)
class Suggestion:
    hero: Hero
    score: float
    reasons: tuple[str, ...]
    components: dict[str, float] = field(default_factory=dict)


class DraftAdvisor:
    def __init__(
        self,
        state: DraftState,
        matchups: dict[int, dict[int, dict[str, int]]] | None = None,
        positions: dict[int, list[float]] | None = None,
        config: EngineConfig = DEFAULT_CONFIG,
        synergy: dict[int, dict[int, tuple[int, int]]] | None = None,
    ):
        self.state = state
        self.config = config
        self.positions = positions if positions is not None else load_hero_positions()
        self.synergy = synergy or {}

        # Build the O(1) matchup lookup once in __init__.
        # Supports both the consolidated dict-of-dicts format and the
        # legacy list-of-rows format for backward compatibility.
        self._matchup_lookup: dict[int, dict[int, tuple[int, int]]] = {}
        for hero_id, opponents in (matchups or {}).items():
            lookup: dict[int, tuple[int, int]] = {}
            if isinstance(opponents, dict):
                for opp_id, row in opponents.items():
                    lookup[int(opp_id)] = (int(row["wins"]), int(row["games"]))
            else:  # legacy list format
                for row in opponents:
                    lookup[int(row["hero_id"])] = (
                        int(row["wins"]),
                        int(row["games_played"]),
                    )
            self._matchup_lookup[int(hero_id)] = lookup

        max_ban = max((s.pro_ban for s in state.hero_stats.values()), default=1)
        max_pick = max((s.pro_pick for s in state.hero_stats.values()), default=1)
        self.max_pro_ban = max_ban or 1
        self.max_pro_pick = max_pick or 1

    def bind(self, state: DraftState) -> "DraftAdvisor":
        """Point this advisor at a newer (immutable) draft state."""
        self.state = state
        return self

    def for_state(self, state: DraftState) -> "DraftAdvisor":
        """Return a lightweight advisor clone bound to a different state.

        Expensive data (matchup lookup, positions, synergy) is shared.
        """
        clone = DraftAdvisor.__new__(DraftAdvisor)
        clone.state = state
        clone.config = self.config
        clone.positions = self.positions
        clone.synergy = self.synergy
        clone._matchup_lookup = self._matchup_lookup
        clone.max_pro_ban = self.max_pro_ban
        clone.max_pro_pick = self.max_pro_pick
        return clone

    # ------------------------------------------------------------------
    # Pairwise matchup helpers (O(1))
    # ------------------------------------------------------------------
    def hero_vs_hero(self, hero_id: int, vs_id: int) -> tuple[float, int]:
        """Winrate of hero_id against vs_id.

        If the direct row is unavailable we use the reverse matchup:
        P(A beats B) ~= 1 - P(B beats A).
        """
        direct = self._matchup_lookup.get(hero_id, {}).get(vs_id)
        if direct is not None:
            wins, games = direct
            return shrunk_winrate(wins, games, self.config), games

        reverse = self._matchup_lookup.get(vs_id, {}).get(hero_id)
        if reverse is not None:
            wins, games = reverse
            return 1.0 - shrunk_winrate(wins, games, self.config), games
        return 0.5, 0

    def hero_with_hero(self, hero_id: int, ally_id: int) -> tuple[float, int]:
        """Shrunk winrate when hero_id and ally_id are on the same team."""
        if hero_id == ally_id:
            return 0.5, 0
        key = (min(hero_id, ally_id), max(hero_id, ally_id))
        row = self.synergy.get(key[0], {}).get(key[1])
        if row is None:
            return 0.5, 0
        wins, games = row
        return shrunk_winrate(wins, games, self.config), games

    # ------------------------------------------------------------------
    # Position / role shape helpers
    # ------------------------------------------------------------------
    def position_probs(self, hero_id: int) -> list[float]:
        if hero_id in self.positions:
            return self.positions[hero_id]
        # Unknown new hero: assume position-flexible rather than crash.
        return [0.2, 0.2, 0.2, 0.2, 0.2]

    def lineup_assignment(
        self, hero_ids: list[int] | tuple[int, ...]
    ) -> dict[int, int] | None:
        result = best_assignment(hero_ids, self.positions)
        return None if result is None else result[1]

    def lineup_quality(self, hero_ids: list[int] | tuple[int, ...]) -> float | None:
        """Average position probability of the best distinct assignment."""
        result = best_assignment(hero_ids, self.positions)
        if result is None:
            return None
        score, _ = result
        return score / len(hero_ids)

    def _lineup_kinds(
        self, hero_ids: list[int] | tuple[int, ...]
    ) -> tuple[int, int, int]:
        core_only = support_only = flex = 0
        for hid in hero_ids:
            kind = role_kind(self.position_probs(hid))
            if kind == "core":
                core_only += 1
            elif kind == "support":
                support_only += 1
            else:
                flex += 1
        return core_only, support_only, flex

    def _candidate_shape_valid(self, hero: Hero, team_picks: tuple[int, ...]) -> bool:
        """Would adding hero still allow a legal 3-core + 2-support lineup?"""
        partial = team_picks + (hero.id,)
        core_only, support_only, flex = self._lineup_kinds(partial)

        if len(partial) == 5:
            return self.lineup_quality(partial) is not None

        remaining = 5 - len(partial)
        used = set(self.state.used_heroes) | {hero.id}
        pool_core = pool_support = pool_flex = 0
        for candidate in self.state.legal_heroes():
            if candidate.id in used:
                continue
            kind = role_kind(self.position_probs(candidate.id))
            if kind == "core":
                pool_core += 1
            elif kind == "support":
                pool_support += 1
            else:
                pool_flex += 1
        return can_finish_roster_shape(
            core_only,
            support_only,
            flex,
            remaining,
            pool_core,
            pool_support,
            pool_flex,
        )

    def _role_contribution(
        self, hero: Hero, team_picks: tuple[int, ...]
    ) -> tuple[float, list[str]]:
        """How much hero improves the best position assignment of a lineup."""
        weights = self.config.scoring
        reasons: list[str] = []
        with_hero = team_picks + (hero.id,)

        if not team_picks:
            quality = self.lineup_quality(with_hero)
            if quality is None:
                return -10.0, ["cannot be assigned to a legal position"]
            return quality * weights.first_pick_position_weight, reasons

        q_before = self.lineup_quality(team_picks)
        q_after = self.lineup_quality(with_hero)
        if q_before is None or q_after is None:
            return -10.0, ["breaks the 3-core / 2-support lineup shape"]

        gain = q_after - q_before
        points = gain * weights.position_gain
        if len(with_hero) == 5 and q_after >= 0.70:
            points += weights.lineup_complete_bonus
            reasons.append("completes a 3-core / 2-support lineup")
        elif gain >= 0.10:
            reasons.append("improves position coverage for the lineup")
        elif gain <= -0.12:
            reasons.append("weak role fit (duplicates a covered position)")

        if len(with_hero) < 5 and role_kind(self.position_probs(hero.id)) == "flex":
            points += weights.flex_bonus
        return points, reasons

    def _role_tag_counts(self, hero_ids: tuple[int, ...]) -> dict[str, int]:
        counts = {role: 0 for role in ROLE_MINIMUMS}
        for hid in hero_ids:
            hero = self.state.heroes.get(hid)
            if not hero:
                continue
            for role in hero.roles:
                if role in counts:
                    counts[role] += 1
        return counts

    def _team_role_gain(self, hero: Hero, team_picks: tuple[int, ...]) -> float:
        """How many missing role-tag slots this hero would fill (0..1)."""
        current = self._role_tag_counts(team_picks)
        gain = 0.0
        total_need = 0
        for role, minimum in ROLE_MINIMUMS.items():
            if minimum <= 0:
                continue
            missing = max(0, minimum - current.get(role, 0))
            total_need += missing
            if missing > 0 and role in hero.roles:
                gain += min(1.0, missing)
        if total_need == 0:
            return 0.0
        return gain / total_need

    def _flex_early_bonus(
        self, hero: Hero, team_picks: tuple[int, ...]
    ) -> tuple[float, list[str]]:
        weights = self.config.scoring
        entropy = position_entropy(self.position_probs(hero.id))
        points = entropy * weights.early_flex_entropy
        reasons: list[str] = []
        if len(team_picks) <= 1 and entropy >= 0.75:
            reasons.append("flexible, playable in core and support roles")
        return points, reasons

    # ------------------------------------------------------------------
    # Meta score
    # ------------------------------------------------------------------
    def meta_score(self, stats: HeroStats) -> tuple[float, list[str]]:
        w = self.config.scoring
        score = 0.0
        reasons: list[str] = []
        if stats.pub_pick >= 50:
            wr = shrunk_winrate(stats.pub_win, stats.pub_pick, self.config)
            pts = (wr - 0.5) * w.meta_pub_winrate
            score += pts
            if pts >= 2.5:
                reasons.append(f"{wr:.0%} pub winrate over {stats.pub_pick:,} games")
            elif pts <= -2.5:
                reasons.append(f"weak pub winrate ({wr:.0%} over {stats.pub_pick:,})")

        ban_pts = (stats.pro_ban / self.max_pro_ban) * w.meta_pro_ban
        score += ban_pts
        if stats.pro_ban >= 30:
            reasons.append(f"high ban priority ({stats.pro_ban} pro bans)")

        pick_pts = (stats.pro_pick / self.max_pro_pick) * w.meta_pro_pick
        score += pick_pts
        if stats.pro_pick >= 25:
            reasons.append(f"meta staple ({stats.pro_pick} pro picks)")

        if stats.pro_pick >= 8:
            wr = shrunk_winrate(stats.pro_win, stats.pro_pick, self.config)
            pts = (wr - 0.5) * w.meta_pro_winrate
            score += pts
            if pts >= 2:
                reasons.append(f"{wr:.0%} pro winrate")
        return score, reasons

    # ------------------------------------------------------------------
    # Pick scoring
    # ------------------------------------------------------------------
    def _pick_score(
        self, hero: Hero, team_picks: tuple[int, ...], enemy_picks: tuple[int, ...]
    ) -> Suggestion:
        w = self.config.scoring
        stats = self.state.hero_stats[hero.id]
        components: dict[str, float] = {}
        reasons: list[str] = []
        min_games = self.config.matchup.min_games_for_reason

        # 1. How well does this hero play into the enemy heroes already shown?
        counter_edges: list[tuple[float, int, str]] = []
        for eid in enemy_picks:
            wr, games = self.hero_vs_hero(hero.id, eid)
            counter_edges.append((wr - 0.5, games, self.state.hero_name(eid)))
        if counter_edges:
            avg_edge = sum(e[0] for e in counter_edges) / len(counter_edges)
            counter_pts = avg_edge * 100.0 * (w.matchup_edge / 100.0)
            components["matchup"] = round(counter_pts, 2)
            worst = min(counter_edges, key=lambda e: e[0])
            if worst[0] <= -0.04 and worst[1] >= min_games:
                reasons.append(
                    f"struggles vs enemy {worst[2]} "
                    f"({(worst[0] + 0.5):.0%}, n={worst[1]})"
                )
            good = [e for e in counter_edges if e[0] >= 0.04 and e[1] >= min_games]
            for edge, games, name in sorted(good, reverse=True)[:2]:
                reasons.append(f"counters enemy {name} ({(edge + 0.5):.0%}, n={games})")

        # 1b. Same-team synergy with our already revealed heroes.
        if team_picks and self.synergy:
            synergy_edges: list[tuple[float, int, str]] = []
            for aid in team_picks:
                wr, games = self.hero_with_hero(hero.id, aid)
                synergy_edges.append((wr - 0.5, games, self.state.hero_name(aid)))
            if synergy_edges:
                avg_edge = sum(e[0] for e in synergy_edges) / len(synergy_edges)
                synergy_pts = avg_edge * 100.0 * (w.team_synergy / 100.0)
                components["synergy"] = round(synergy_pts, 2)
                good = [e for e in synergy_edges if e[0] >= 0.04 and e[1] >= min_games]
                for edge, games, name in sorted(good, reverse=True)[:1]:
                    reasons.append(
                        f"strong pairing with {name} ({(edge + 0.5):.0%}, n={games})"
                    )

        # 2. Core/support lineup shape (positions 1-5).
        shape_points, shape_reasons = self._role_contribution(hero, team_picks)
        components["position"] = round(shape_points, 2)
        reasons.extend(shape_reasons)

        # 3. Early-pick flexibility (Io, Windranger, etc.).
        flex_points, flex_reasons = self._flex_early_bonus(hero, team_picks)
        components["flexibility"] = round(flex_points, 2)
        reasons.extend(flex_reasons)

        # 4. Missing role tags (stun/initiation/waveclear/durable etc.).
        role_gain = self._team_role_gain(hero, team_picks)
        if role_gain > 0:
            pts = role_gain * w.role_tag_gain
            components["role_tags"] = round(pts, 2)
            if pts >= 5:
                missing = []
                current = self._role_tag_counts(team_picks)
                for role, minimum in ROLE_MINIMUMS.items():
                    if current.get(role, 0) < minimum and role in hero.roles:
                        missing.append(role.lower())
                if missing:
                    reasons.append("adds " + ", ".join(missing[:2]))

        # 5. Meta strength.
        meta_pts, meta_reasons = self.meta_score(stats)
        components["meta"] = round(meta_pts, 2)
        reasons.extend(meta_reasons[:1])

        # Make sure every suggestion explains at least something.
        if not reasons:
            if stats.pro_ban >= 10:
                reasons.append("respected meta hero")
            elif stats.pro_pick >= 10:
                reasons.append("solid meta presence")
            else:
                reasons.append("situational pick")

        total = round(sum(components.values()), 2)
        return Suggestion(
            hero=hero,
            score=total,
            reasons=tuple(reasons[:4]),
            components=components,
        )

    # ------------------------------------------------------------------
    # Ban scoring
    # ------------------------------------------------------------------
    def _ban_score(
        self, hero: Hero, my_picks: tuple[int, ...], enemy_picks: tuple[int, ...]
    ) -> Suggestion:
        w = self.config.scoring
        stats = self.state.hero_stats[hero.id]
        components: dict[str, float] = {}
        reasons: list[str] = []
        min_games = self.config.matchup.min_games_for_reason

        # 1. Ban heroes that counter the heroes we have already committed to.
        threats: list[tuple[float, int, str]] = []
        for mid in my_picks:
            wr, games = self.hero_vs_hero(hero.id, mid)
            threats.append((wr - 0.5, games, self.state.hero_name(mid)))
        if threats:
            threat = max(threats, key=lambda t: t[0])
            pts = max(0.0, threat[0]) * 100.0 * (w.ban_counter_threat / 100.0)
            components["counter_threat"] = round(pts, 2)
            if pts >= 4 and threat[1] >= min_games:
                reasons.append(
                    f"counters our {threat[2]} ({(threat[0] + 0.5):.0%}, n={threat[1]})"
                )

        # 1b. Deny heroes that complete strong pairings for the enemy.
        if enemy_picks and self.synergy:
            synergy_edges = []
            for eid in enemy_picks:
                wr, games = self.hero_with_hero(hero.id, eid)
                synergy_edges.append((wr - 0.5, games, self.state.hero_name(eid)))
            if synergy_edges:
                avg_edge = sum(e[0] for e in synergy_edges) / len(synergy_edges)
                pts = max(0.0, avg_edge) * 100.0 * (w.ban_enemy_synergy / 100.0)
                components["enemy_synergy"] = round(pts, 2)
                good = [e for e in synergy_edges if e[0] >= 0.05 and e[1] >= min_games]
                for edge, games, name in sorted(good, reverse=True)[:1]:
                    reasons.append(
                        f"completes strong enemy pairing with {name} "
                        f"({(edge + 0.5):.0%}, n={games})"
                    )

        # 2. Deny a hero that fits the positions the enemy still needs.
        if enemy_picks:
            enemy_gain, enemy_reason = self._role_contribution(hero, enemy_picks)
            pts = max(0.0, enemy_gain) * w.ban_position_denial_multiplier
            components["position_denial"] = round(pts, 2)
            if enemy_gain >= 6 and enemy_reason:
                reasons.append("denies a position the enemy still needs")
        else:
            # Early ban phase: flexible heroes are the most likely to be
            # contested by either side.
            pts = position_entropy(self.position_probs(hero.id)) * w.ban_early_entropy
            components["early_flex"] = round(pts, 2)

        # 3. Meta ban priority.
        meta_pts, meta_reasons = self.meta_score(stats)
        pts = meta_pts * w.ban_meta_multiplier
        components["meta"] = round(pts, 2)
        for r in meta_reasons[:1]:
            if "ban priority" in r:
                reasons.append(r)
            elif "pub winrate" in r:
                reasons.append(r)

        # 4. Deny enemy role-tag coverage.
        role_gain = self._team_role_gain(hero, enemy_picks)
        if role_gain > 0:
            pts = role_gain * w.ban_role_tag_gain
            components["role_tags"] = round(pts, 2)
            if pts >= 3:
                reasons.append("denies initiation/disable coverage")

        if not reasons:
            reasons.append("meta-consistency ban")

        total = round(sum(components.values()), 2)
        return Suggestion(
            hero=hero,
            score=total,
            reasons=tuple(reasons[:4]),
            components=components,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def suggest_picks(self, side: Side, limit: int = 5) -> list[Suggestion]:
        my_picks = self.state.side_picks(side)
        enemy_side: Side = "dire" if side == "radiant" else "radiant"
        enemy_picks = self.state.side_picks(enemy_side)
        out = []
        for hero in self.state.legal_heroes():
            if not self._candidate_shape_valid(hero, my_picks):
                continue
            out.append(self._pick_score(hero, my_picks, enemy_picks))
        out.sort(key=lambda s: (-s.score, -self.state.hero_stats[s.hero.id].pro_ban))
        return out[:limit]

    def suggest_bans(self, side: Side, limit: int = 5) -> list[Suggestion]:
        my_picks = self.state.side_picks(side)
        enemy_side: Side = "dire" if side == "radiant" else "radiant"
        enemy_picks = self.state.side_picks(enemy_side)
        out = []
        for hero in self.state.legal_heroes():
            out.append(self._ban_score(hero, my_picks, enemy_picks))
        out.sort(key=lambda s: (-s.score, -self.state.hero_stats[s.hero.id].pro_ban))
        return out[:limit]

    def best_pick(self, side: Side) -> Suggestion:
        suggestions = self.suggest_picks(side, limit=1)
        if not suggestions:
            raise SuggestionError("no legal pick satisfies the 3-core/2-support shape")
        return suggestions[0]

    def best_ban(self, side: Side) -> Suggestion:
        suggestions = self.suggest_bans(side, limit=1)
        if not suggestions:
            raise SuggestionError("no legal ban is available")
        return suggestions[0]

    # ------------------------------------------------------------------
    # Per-hero analysis (used by the web visualizer)
    # ------------------------------------------------------------------
    def analyze_hero(
        self,
        hero_id: int,
        side: Side | None = None,
        action: Action | None = None,
    ) -> dict:
        hero = self.state.heroes[hero_id]
        probs = self.position_probs(hero.id)
        kind = role_kind(probs)
        turn = self.state.current_turn
        side = side or (
            self.state.team_side(turn.team) if turn is not None else "radiant"
        )
        action = action or (turn.action if turn is not None else "pick")
        enemy_side: Side = "dire" if side == "radiant" else "radiant"
        my_picks = self.state.side_picks(side)
        enemy_picks = self.state.side_picks(enemy_side)

        availability = "available"
        for check_side in ("radiant", "dire"):
            if hero.id in self.state.side_picks(check_side):
                availability = f"picked_{check_side}"
            elif hero.id in self.state.side_bans(check_side):
                availability = f"banned_{check_side}"

        meta_pts, meta_reasons = self.meta_score(self.state.hero_stats[hero.id])

        matchups: list[dict[str, Any]] = []
        for eid in enemy_picks:
            wr, games = self.hero_vs_hero(hero.id, eid)
            matchups.append(
                {
                    "enemy_id": eid,
                    "enemy": self.state.hero_name(eid),
                    "winrate": round(wr, 4),
                    "games": games,
                    "edge": round(wr - 0.5, 4),
                }
            )
        matchups.sort(key=lambda row: row["edge"])

        synergies: list[dict[str, Any]] = []
        for aid in my_picks:
            wr, games = self.hero_with_hero(hero.id, aid)
            synergies.append(
                {
                    "ally_id": aid,
                    "ally": self.state.hero_name(aid),
                    "winrate": round(wr, 4),
                    "games": games,
                    "edge": round(wr - 0.5, 4),
                }
            )
        synergies.sort(key=lambda row: -row["edge"])

        if action == "pick":
            suggestion = self._pick_score(hero, my_picks, enemy_picks)
            shape_valid = self._candidate_shape_valid(hero, my_picks)
        else:
            suggestion = self._ban_score(hero, my_picks, enemy_picks)
            shape_valid = None

        after = my_picks + (hero.id,)
        lineup_after = self.lineup_quality(after) if len(after) <= 5 else None

        return {
            "hero_id": hero.id,
            "name": hero.display_name,
            "action": action,
            "side": side,
            "availability": availability,
            "role": kind,
            "core_pct": round(sum(probs[:3]) * 100, 1),
            "support_pct": round(sum(probs[3:]) * 100, 1),
            "pos_probs": [round(p * 100, 1) for p in probs],
            "score": suggestion.score,
            "reasons": list(suggestion.reasons),
            "components": suggestion.components,
            "shape_valid": shape_valid,
            "lineup_quality_after": (
                round(lineup_after, 3) if lineup_after is not None else None
            ),
            "meta": {
                "score": round(meta_pts, 2),
                "reasons": meta_reasons,
                "pro_ban": self.state.hero_stats[hero.id].pro_ban,
                "pro_pick": self.state.hero_stats[hero.id].pro_pick,
            },
            "matchups": matchups,
            "synergies": synergies,
        }

    # ------------------------------------------------------------------
    # Final lineup report
    # ------------------------------------------------------------------
    def describe_lineup(self, side: Side) -> str:
        picks = self.state.side_picks(side)
        assignment = self.lineup_assignment(picks) if len(picks) == 5 else None
        if assignment is None:
            return "  role assignment: not available"
        by_pos = sorted(assignment.items(), key=lambda kv: kv[1])
        parts = []
        for hid, pos in by_pos:
            label = "core" if pos <= 3 else "support"
            parts.append(f"pos{pos} {self.state.hero_name(hid)} ({label})")
        core_count = sum(1 for _, pos in by_pos if pos <= 3)
        support_count = len(by_pos) - core_count
        return (
            f"  {side.title():7s}: "
            + ", ".join(parts)
            + f"  => {core_count} core, {support_count} support"
        )
