"""Position (1-5) role model for heroes.

OpenDota's `heroStats` fields `1_pick`..`8_pick` are **rank brackets**,
not positions, so the prototype no longer uses them for roles.

Instead we infer positions from parsed `player_matches`:

  * lane_role 2                    -> position 2
  * lane_role 4 or is_roaming      -> position 4
  * lane_role 1 (safe lane):  the higher-GPM player on that side -> pos 1,
                              the lower-GPM player               -> pos 5
  * lane_role 3 (off lane):   the higher-GPM player on that side -> pos 3,
                              the lower-GPM player               -> pos 4

The aggregate data is cached in cache/position_roles_raw.json (generated
with OpenDota's Explorer SQL endpoint from the most recent matches).

Heroes with very few observations fall back to a small curated map so a
single off-meta game doesn't reclassify a hero.
"""
from __future__ import annotations

import json
from pathlib import Path

from .data import CACHE_DIR, _http_get_json

POSITIONS = (1, 2, 3, 4, 5)
CORE_POSITIONS = (1, 2, 3)
SUPPORT_POSITIONS = (4, 5)
DIRICHLET_PRIOR = 0.35      # smoothing added to every position
MIN_POSITION_PROB = 0.05    # below this a hero is considered ineligible
MIN_ROLE_PROB = 0.08        # below this core/support capability is ignored
MIN_EMPIRICAL_GAMES = 10    # below this use the curated fallback

# Curated fallback positions for heroes absent/rare in the recent sample.
# Values are position lists; every entry in a list gets equal weight.
CURATED_POSITIONS: dict[int, list[int]] = {
    1: [1], 4: [1], 5: [5], 8: [1], 15: [1, 2, 3], 22: [2, 4],
    23: [2, 3], 26: [4, 5], 32: [1, 4], 34: [2], 35: [2], 37: [5],
    40: [4, 5], 42: [1, 3], 43: [2, 3], 44: [1], 45: [4, 5],
    50: [5], 52: [2], 57: [5], 61: [1, 3], 63: [1, 4], 66: [4, 5],
    68: [4, 5], 70: [1], 75: [4, 5], 76: [2], 81: [1], 82: [1, 2],
    84: [4, 5], 92: [2, 4], 94: [1], 95: [1], 99: [3], 101: [4, 2],
    102: [3, 5], 104: [3], 113: [2], 129: [3], 137: [2, 3], 138: [1, 4],
}


def _raw_path() -> Path:
    return CACHE_DIR / "position_roles_raw.json"


def fetch_position_roles() -> None:
    """(Re)build cache/position_roles_raw.json from OpenDota Explorer."""
    sql = (
        "SELECT hero_id, pos, count(*) AS games FROM ("
        "SELECT hero_id, CASE "
        "WHEN lane_role = 2 THEN 2 "
        "WHEN lane_role = 4 OR is_roaming THEN 4 "
        "WHEN lane_role = 1 THEN CASE WHEN row_number() OVER "
        "(PARTITION BY match_id, player_slot / 128, lane_role "
        "ORDER BY gold_per_min DESC) = 1 THEN 1 ELSE 5 END "
        "WHEN lane_role = 3 THEN CASE WHEN row_number() OVER "
        "(PARTITION BY match_id, player_slot / 128, lane_role "
        "ORDER BY gold_per_min DESC) = 1 THEN 3 ELSE 4 END "
        "ELSE 0 END AS pos FROM player_matches "
        "WHERE match_id > 8900000000 AND lane_role IN (1,2,3,4)"
        ") t WHERE pos IN (1,2,3,4,5) "
        "GROUP BY hero_id, pos ORDER BY hero_id, games DESC"
    )
    import urllib.parse
    url = f"https://api.opendota.com/api/explorer?sql={urllib.parse.quote(sql)}"
    print("Downloading empirical position data from OpenDota Explorer...")
    data = _http_get_json(url, timeout=90)
    if data.get("err"):
        raise RuntimeError(f"explorer query failed: {data['err']}")
    path = _raw_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _fallback_probs(hero_id: int) -> list[float]:
    positions = CURATED_POSITIONS.get(hero_id, [2, 4])  # generic flex fallback
    # A small non-position baseline keeps assignments possible, but 90% of
    # the mass stays on the curated positions so a carry isn't labeled flex
    # just because it lacks recent observations.
    probs = [0.02] * 5
    weight = 0.9
    for pos in positions:
        probs[pos - 1] += weight / len(positions)
    return probs


def load_hero_positions() -> dict[int, list[float]]:
    """Return hero_id -> probability distribution over positions 1..5."""
    path = _raw_path()
    if not path.exists():
        fetch_position_roles()

    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("rows", [])
    empirical: dict[int, dict[int, int]] = {}
    for row in rows:
        hid = int(row["hero_id"])
        pos = int(row["pos"])
        games = int(row["games"])
        empirical.setdefault(hid, {})[pos] = games

    hero_ids = set(empirical) | set(CURATED_POSITIONS)
    out: dict[int, list[float]] = {}
    for hid in sorted(hero_ids):
        counts = empirical.get(hid, {})
        total = sum(counts.values())
        if total >= MIN_EMPIRICAL_GAMES:
            probs = []
            for pos in POSITIONS:
                probs.append((counts.get(pos, 0) + DIRICHLET_PRIOR)
                             / (total + 5 * DIRICHLET_PRIOR))
            out[hid] = probs
        else:
            out[hid] = _fallback_probs(hid)
    return out


def core_prob(probs: list[float]) -> float:
    return sum(probs[i - 1] for i in CORE_POSITIONS)


def support_prob(probs: list[float]) -> float:
    return sum(probs[i - 1] for i in SUPPORT_POSITIONS)


def position_entropy(probs: list[float]) -> float:
    import math
    return -sum(p * math.log(p) for p in probs if p > 0) / math.log(5)


def is_core_capable(probs: list[float]) -> bool:
    return core_prob(probs) >= MIN_ROLE_PROB


def is_support_capable(probs: list[float]) -> bool:
    return support_prob(probs) >= MIN_ROLE_PROB


def role_kind(probs: list[float]) -> str:
    core = is_core_capable(probs)
    support = is_support_capable(probs)
    if core and support:
        return "flex"
    if core:
        return "core"
    if support:
        return "support"
    return "flex"


def eligible_positions(probs: list[float]) -> list[int]:
    return [p for p in POSITIONS if probs[p - 1] >= MIN_POSITION_PROB]


def best_assignment(hero_ids: list[int],
                    positions: dict[int, list[float]]) -> tuple[float, dict[int, int]] | None:
    """Best distinct-position assignment for up to 5 heroes.

    Returns (sum of position probabilities, {hero_id: position}) or None
    when no assignment respecting MIN_POSITION_PROB exists.
    """
    if not hero_ids:
        return 0.0, {}
    if len(hero_ids) > 5:
        raise ValueError("a Dota lineup has at most 5 heroes")

    best = {"score": -1.0, "assignment": {}}

    def rec(i: int, used: set[int], score: float, assignment: dict[int, int]) -> None:
        if score + (len(hero_ids) - i) * 1.0 <= best["score"]:
            return
        if i == len(hero_ids):
            if score > best["score"]:
                best["score"] = score
                best["assignment"] = dict(assignment)
            return
        hid = hero_ids[i]
        for pos in eligible_positions(positions.get(hid, [])):
            if pos in used:
                continue
            used.add(pos)
            assignment[hid] = pos
            rec(i + 1, used, score + positions[hid][pos - 1], assignment)
            del assignment[hid]
            used.remove(pos)

    rec(0, set(), 0.0, {})
    if best["score"] < 0:
        return None
    return best["score"], best["assignment"]


def can_finish_roster_shape(core_only: int, support_only: int, flex: int,
                            remaining_picks: int,
                            pool_core_only: int, pool_support_only: int,
                            pool_flex: int) -> bool:
    """Exact type-level feasibility for the remaining picks.

    Future picks are chosen from the remaining hero pool.  core_only and
    support_only heroes are committed to one role; flex heroes may fill
    either.  We iterate over how many future core-only/support-only/flex
    heroes are taken and check the final 3-core / 2-support shape is
    reachable.
    """
    if core_only > 3 or support_only > 2:
        return False
    for c_future in range(0, min(pool_core_only, remaining_picks) + 1):
        for s_future in range(0, min(pool_support_only, remaining_picks - c_future) + 1):
            f_future = remaining_picks - c_future - s_future
            if f_future > pool_flex:
                continue
            c_total = core_only + c_future
            s_total = support_only + s_future
            f_total = flex + f_future
            if c_total <= 3 and s_total <= 2 and c_total + s_total + f_total == 5:
                return True
    return False
