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

from dataclasses import dataclass

from .models import DraftState, Hero, HeroStats
from .roles import (
    best_assignment,
    can_finish_roster_shape,
    core_prob,
    eligible_positions,
    is_core_capable,
    is_support_capable,
    load_hero_positions,
    position_entropy,
    role_kind,
    support_prob,
)

PSEUDO_COUNT = 20          # Bayesian shrinkage strength for small samples
MIN_GAMES_FOR_REASON = 10  # don't brag about a 5-game sample

# Non-core/support role tags we still want in a well-rounded draft.
ROLE_MINIMUMS: dict[str, int] = {
    "Initiator": 1,
    "Disabler": 2,
    "Nuker": 2,
    "Durable": 1,
    "Pusher": 1,
    "Escape": 0,
}


def shrunk_winrate(wins: int, games: int, prior: float = 0.5) -> float:
    if games <= 0:
        return prior
    return (wins + PSEUDO_COUNT * prior) / (games + PSEUDO_COUNT)


@dataclass
class Suggestion:
    hero: Hero
    score: float
    reasons: list[str]


class DraftAdvisor:
    def __init__(self, state: DraftState,
                 matchups: dict[int, list[dict]] | None = None,
                 positions: dict[int, list[float]] | None = None):
        self.state = state
        self.matchups = matchups or {}
        self.positions = positions if positions is not None else load_hero_positions()

        max_ban = max((s.pro_ban for s in state.hero_stats.values()), default=1)
        max_pick = max((s.pro_pick for s in state.hero_stats.values()), default=1)
        self.max_pro_ban = max_ban or 1
        self.max_pro_pick = max_pick or 1

    # ------------------------------------------------------------------
    # Pairwise matchup helpers
    # ------------------------------------------------------------------
    def _direct_row(self, hero_id: int, vs_id: int) -> tuple[float, int]:
        for row in self.matchups.get(hero_id, []):
            if int(row.get("hero_id")) == vs_id:
                return float(row.get("wins", 0)), int(row.get("games_played", 0))
        return 0.0, 0

    def hero_vs_hero(self, hero_id: int, vs_id: int) -> tuple[float, int]:
        """Winrate of hero_id against vs_id.

        If the direct row is unavailable we use the reverse matchup:
        P(A beats B) ~= 1 - P(B beats A).
        """
        wins, games = self._direct_row(hero_id, vs_id)
        if games > 0:
            return shrunk_winrate(wins, games), games
        rwins, rgames = self._direct_row(vs_id, hero_id)
        if rgames > 0:
            return 1.0 - shrunk_winrate(rwins, rgames), rgames
        return 0.5, 0

    # ------------------------------------------------------------------
    # Position / role shape helpers
    # ------------------------------------------------------------------
    def position_probs(self, hero_id: int) -> list[float]:
        if hero_id in self.positions:
            return self.positions[hero_id]
        # Unknown new hero: assume position-flexible rather than crash.
        return [0.2, 0.2, 0.2, 0.2, 0.2]

    def lineup_assignment(self, hero_ids: list[int]) -> dict[int, int] | None:
        result = best_assignment(hero_ids, self.positions)
        return None if result is None else result[1]

    def lineup_quality(self, hero_ids: list[int]) -> float | None:
        """Average position probability of the best distinct assignment."""
        result = best_assignment(hero_ids, self.positions)
        if result is None:
            return None
        score, _ = result
        return score / len(hero_ids)

    def _lineup_kinds(self, hero_ids: list[int]) -> tuple[int, int, int]:
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

    def _candidate_shape_valid(self, hero: Hero, team_picks: list[int]) -> bool:
        """Would adding hero still allow a legal 3-core + 2-support lineup?"""
        partial = team_picks + [hero.id]
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
            core_only, support_only, flex, remaining,
            pool_core, pool_support, pool_flex,
        )

    def _role_contribution(self, hero: Hero, team_picks: list[int]) -> tuple[float, list[str]]:
        """How much hero improves the best position assignment of a lineup."""
        reasons: list[str] = []
        with_hero = team_picks + [hero.id]

        if not team_picks:
            # First pick: specialist heroes score well on raw fit, but
            # flexible heroes receive a separate early-pick bonus below.
            quality = self.lineup_quality(with_hero)
            if quality is None:
                return -10.0, ["cannot be assigned to a legal position"]
            return quality * 10.0, reasons

        q_before = self.lineup_quality(team_picks)
        q_after = self.lineup_quality(with_hero)
        if q_before is None or q_after is None:
            return -10.0, ["breaks the 3-core / 2-support lineup shape"]

        gain = q_after - q_before
        points = gain * 35.0
        if len(with_hero) == 5 and q_after is not None and q_after >= 0.70:
            points += 4.0
            reasons.append("completes a 3-core / 2-support lineup")
        elif gain >= 0.10:
            reasons.append("improves position coverage for the lineup")
        elif gain <= -0.12:
            reasons.append("weak role fit (duplicates a covered position)")

        kind = role_kind(self.position_probs(hero.id))
        if len(with_hero) < 5 and kind == "flex":
            points += 1.0
        return points, reasons

    def _role_tag_counts(self, hero_ids: list[int]) -> dict[str, int]:
        counts = {role: 0 for role in ROLE_MINIMUMS}
        for hid in hero_ids:
            hero = self.state.heroes.get(hid)
            if not hero:
                continue
            for role in hero.roles:
                if role in counts:
                    counts[role] += 1
        return counts

    def _team_role_gain(self, hero: Hero, team_picks: list[int]) -> float:
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

    def _flex_early_bonus(self, hero: Hero, team_picks: list[int]) -> tuple[float, list[str]]:
        entropy = position_entropy(self.position_probs(hero.id))
        points = entropy * 8.0
        reasons: list[str] = []
        if len(team_picks) <= 1 and entropy >= 0.75:
            reasons.append("flexible, playable in core and support roles")
        return points, reasons

    # ------------------------------------------------------------------
    # Meta score
    # ------------------------------------------------------------------
    def meta_score(self, stats: HeroStats) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        if stats.pub_pick >= 50:
            wr = shrunk_winrate(stats.pub_win, stats.pub_pick)
            pts = (wr - 0.5) * 60.0
            score += pts
            if pts >= 2.5:
                reasons.append(
                    f"{wr:.0%} pub winrate over {stats.pub_pick:,} games"
                )
            elif pts <= -2.5:
                reasons.append(
                    f"weak pub winrate ({wr:.0%} over {stats.pub_pick:,})"
                )

        if self.max_pro_ban:
            ban_pts = (stats.pro_ban / self.max_pro_ban) * 30.0
            score += ban_pts
            if stats.pro_ban >= 30:
                reasons.append(f"high ban priority ({stats.pro_ban} pro bans)")
        if self.max_pro_pick:
            pick_pts = (stats.pro_pick / self.max_pro_pick) * 15.0
            score += pick_pts
            if stats.pro_pick >= 25:
                reasons.append(f"meta staple ({stats.pro_pick} pro picks)")
        if stats.pro_pick >= 8:
            wr = shrunk_winrate(stats.pro_win, stats.pro_pick)
            pts = (wr - 0.5) * 20.0
            score += pts
            if pts >= 2:
                reasons.append(f"{wr:.0%} pro winrate")
        return score, reasons

    # ------------------------------------------------------------------
    # Pick scoring
    # ------------------------------------------------------------------
    def _pick_score(self, hero: Hero, team_picks: list[int],
                    enemy_picks: list[int]) -> Suggestion:
        stats = self.state.hero_stats[hero.id]
        score = 0.0
        reasons: list[str] = []

        # 1. How well does this hero play into the enemy heroes already shown?
        counter_edges: list[tuple[float, int, str]] = []
        for eid in enemy_picks:
            wr, games = self.hero_vs_hero(hero.id, eid)
            edge = wr - 0.5
            counter_edges.append((edge, games, self.state.hero_name(eid)))
        if counter_edges:
            avg_edge = sum(e[0] for e in counter_edges) / len(counter_edges)
            score += avg_edge * 100.0 * 0.9
            worst = min(counter_edges, key=lambda e: e[0])
            if worst[0] <= -0.04 and worst[1] >= MIN_GAMES_FOR_REASON:
                reasons.append(
                    f"struggles vs enemy {worst[2]} "
                    f"({(worst[0] + 0.5):.0%}, n={worst[1]})"
                )
            good = [e for e in counter_edges
                    if e[0] >= 0.04 and e[1] >= MIN_GAMES_FOR_REASON]
            for edge, games, name in sorted(good, reverse=True)[:2]:
                reasons.append(
                    f"counters enemy {name} ({(edge + 0.5):.0%}, n={games})"
                )

        # 2. Core/support lineup shape (positions 1-5).
        shape_points, shape_reasons = self._role_contribution(hero, team_picks)
        score += shape_points
        reasons.extend(shape_reasons)

        # 3. Early-pick flexibility (Io, Windranger, etc.).
        flex_points, flex_reasons = self._flex_early_bonus(hero, team_picks)
        score += flex_points
        reasons.extend(flex_reasons)

        # 4. Missing role tags (stun/initiation/waveclear/durable etc.).
        role_gain = self._team_role_gain(hero, team_picks)
        if role_gain > 0:
            pts = role_gain * 12.0
            score += pts
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
        score += meta_pts
        reasons.extend(meta_reasons[:1])

        # Make sure every suggestion explains at least something.
        if not reasons:
            if stats.pro_ban >= 10:
                reasons.append("respected meta hero")
            elif stats.pro_pick >= 10:
                reasons.append("solid meta presence")
            else:
                reasons.append("situational pick")

        return Suggestion(hero=hero, score=round(score, 2), reasons=reasons[:4])

    # ------------------------------------------------------------------
    # Ban scoring
    # ------------------------------------------------------------------
    def _ban_score(self, hero: Hero, my_picks: list[int],
                   enemy_picks: list[int]) -> Suggestion:
        stats = self.state.hero_stats[hero.id]
        score = 0.0
        reasons: list[str] = []

        # 1. Ban heroes that counter the heroes we have already committed to.
        threats: list[tuple[float, int, str]] = []
        for mid in my_picks:
            wr, games = self.hero_vs_hero(hero.id, mid)
            threats.append((wr - 0.5, games, self.state.hero_name(mid)))
        if threats:
            threat = max(threats, key=lambda t: t[0])
            pts = max(0.0, threat[0]) * 100.0 * 1.2
            score += pts
            if pts >= 4 and threat[1] >= MIN_GAMES_FOR_REASON:
                reasons.append(
                    f"counters our {threat[2]} ({(threat[0] + 0.5):.0%}, n={threat[1]})"
                )

        # 2. Deny a hero that fits the positions the enemy still needs.
        if enemy_picks:
            enemy_gain, enemy_reason = self._role_contribution(hero, enemy_picks)
            score += max(0.0, enemy_gain) * 0.9
            if enemy_gain >= 6 and enemy_reason:
                reasons.append("denies a position the enemy still needs")
        else:
            # Early ban phase: flexible heroes are the most likely to be
            # contested by either side.
            score += position_entropy(self.position_probs(hero.id)) * 4.0

        # 3. Meta ban priority.
        meta_pts, meta_reasons = self.meta_score(stats)
        score += meta_pts * 0.9
        for r in meta_reasons[:1]:
            if "ban priority" in r:
                reasons.append(r)
            elif "pub winrate" in r:
                reasons.append(r)

        # 4. Deny enemy role-tag coverage.
        role_gain = self._team_role_gain(hero, enemy_picks)
        if role_gain > 0:
            pts = role_gain * 8.0
            score += pts
            if pts >= 3:
                reasons.append("denies initiation/disable coverage")

        if not reasons:
            reasons.append("meta-consistency ban")

        return Suggestion(hero=hero, score=round(score, 2), reasons=reasons[:4])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def suggest_picks(self, side: str, limit: int = 5) -> list[Suggestion]:
        my_picks = self.state.side_picks(side)
        enemy_side = "dire" if side == "radiant" else "radiant"
        enemy_picks = self.state.side_picks(enemy_side)
        out = []
        for hero in self.state.legal_heroes():
            if not self._candidate_shape_valid(hero, my_picks):
                continue
            out.append(self._pick_score(hero, my_picks, enemy_picks))
        out.sort(key=lambda s: (-s.score, -self.state.hero_stats[s.hero.id].pro_ban))
        return out[:limit]

    def suggest_bans(self, side: str, limit: int = 5) -> list[Suggestion]:
        my_picks = self.state.side_picks(side)
        enemy_side = "dire" if side == "radiant" else "radiant"
        enemy_picks = self.state.side_picks(enemy_side)
        out = []
        for hero in self.state.legal_heroes():
            out.append(self._ban_score(hero, my_picks, enemy_picks))
        out.sort(key=lambda s: (-s.score, -self.state.hero_stats[s.hero.id].pro_ban))
        return out[:limit]

    def best_pick(self, side: str) -> Suggestion:
        suggestions = self.suggest_picks(side, limit=1)
        if not suggestions:
            raise RuntimeError("no legal pick satisfies the 3-core/2-support shape")
        return suggestions[0]

    def best_ban(self, side: str) -> Suggestion:
        return self.suggest_bans(side, limit=1)[0]

    # ------------------------------------------------------------------
    # Final lineup report
    # ------------------------------------------------------------------
    def describe_lineup(self, side: str) -> str:
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
        return (f"  {side.title():7s}: " + ", ".join(parts)
                + f"  => {core_count} core, {support_count} support")
