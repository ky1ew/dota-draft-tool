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
import logging
import math
import urllib.parse
from functools import lru_cache
from pathlib import Path

from .config import DEFAULT_CONFIG, RoleConfig
from .data import CACHE_DIR, _http_get_json
from .exceptions import DataFetchError

logger = logging.getLogger(__name__)

POSITIONS = (1, 2, 3, 4, 5)
CORE_POSITIONS = (1, 2, 3)
SUPPORT_POSITIONS = (4, 5)

# Curated fallback positions for heroes absent/rare in the recent sample.
# Values are position lists; every entry in a list gets equal weight.
CURATED_POSITIONS: dict[int, list[int]] = {
    1: [1],
    4: [1],
    5: [5],
    8: [1],
    15: [1, 2, 3],
    22: [2, 4],
    23: [2, 3],
    26: [4, 5],
    32: [1, 4],
    34: [2],
    35: [2],
    37: [5],
    40: [4, 5],
    42: [1, 3],
    43: [2, 3],
    44: [1],
    45: [4, 5],
    50: [5],
    52: [2],
    57: [5],
    61: [1, 3],
    63: [1, 4],
    66: [4, 5],
    68: [4, 5],
    70: [1],
    75: [4, 5],
    76: [2],
    81: [1],
    82: [1, 2],
    84: [4, 5],
    92: [2, 4],
    94: [1],
    95: [1],
    99: [3],
    101: [4, 2],
    102: [3, 5],
    104: [3],
    113: [2],
    129: [3],
    137: [2, 3],
    138: [1, 4],
}


def _raw_path() -> Path:
    return CACHE_DIR / "position_roles_raw.json"


def fetch_position_roles() -> None:
    """(Re)build cache/position_roles_raw.json from OpenDota Explorer."""
    min_match_id = DEFAULT_CONFIG.data.position_min_match_id
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
        f"WHERE match_id > {min_match_id} AND lane_role IN (1,2,3,4)"
        ") t WHERE pos IN (1,2,3,4,5) "
        "GROUP BY hero_id, pos ORDER BY hero_id, games DESC"
    )
    url = f"https://api.opendota.com/api/explorer?sql={urllib.parse.quote(sql)}"
    logger.info("Downloading empirical position data from OpenDota Explorer...")
    data = _http_get_json(url, timeout=DEFAULT_CONFIG.data.explorer_timeout)
    if data.get("err"):
        raise DataFetchError("OpenDota Explorer position query", data["err"])
    path = _raw_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _fallback_probs(hero_id: int, cfg: RoleConfig) -> list[float]:
    positions = CURATED_POSITIONS.get(hero_id, [2, 4])  # generic flex fallback
    # A small non-position baseline keeps assignments possible, but most
    # mass stays on the curated positions so a carry isn't labeled flex
    # just because it lacks recent observations.
    probs = [cfg.fallback_base_prob] * 5
    weight = cfg.fallback_weight
    for pos in positions:
        probs[pos - 1] += weight / len(positions)
    return probs


def load_hero_positions() -> dict[int, list[float]]:
    """Return hero_id -> probability distribution over positions 1..5."""
    cfg = DEFAULT_CONFIG.roles
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
        if total >= cfg.min_empirical_games:
            probs = []
            for pos in POSITIONS:
                probs.append(
                    (counts.get(pos, 0) + cfg.dirichlet_prior)
                    / (total + 5 * cfg.dirichlet_prior)
                )
            out[hid] = probs
        else:
            out[hid] = _fallback_probs(hid, cfg)
    return out


def core_prob(probs: list[float]) -> float:
    return sum(probs[i - 1] for i in CORE_POSITIONS)


def support_prob(probs: list[float]) -> float:
    return sum(probs[i - 1] for i in SUPPORT_POSITIONS)


def position_entropy(probs: list[float]) -> float:
    return -sum(p * math.log(p) for p in probs if p > 0) / math.log(5)


def is_core_capable(probs: list[float]) -> bool:
    return core_prob(probs) >= DEFAULT_CONFIG.roles.min_role_prob


def is_support_capable(probs: list[float]) -> bool:
    return support_prob(probs) >= DEFAULT_CONFIG.roles.min_role_prob


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
    threshold = DEFAULT_CONFIG.roles.min_position_prob
    return [p for p in POSITIONS if probs[p - 1] >= threshold]


@lru_cache(maxsize=DEFAULT_CONFIG.roles.assignment_cache_size)
def _cached_best_assignment(
    hero_ids: tuple[int, ...],
    pos_vectors: tuple[tuple[float, ...], ...],
) -> tuple[float, tuple[tuple[int, int], ...]] | None:
    """Cached core of best_assignment (args must be hashable)."""
    threshold = DEFAULT_CONFIG.roles.min_position_prob
    eligible = [
        [p for p in POSITIONS if vec[p - 1] >= threshold] for vec in pos_vectors
    ]

    best_score = -1.0
    best_assign: tuple[tuple[int, int], ...] | None = None

    def rec(i: int, used: int, score: float, assignment: list[tuple[int, int]]) -> None:
        nonlocal best_score, best_assign
        if score + (len(hero_ids) - i) <= best_score:
            return
        if i == len(hero_ids):
            if score > best_score:
                best_score = score
                best_assign = tuple(assignment)
            return
        hid = hero_ids[i]
        for pos in eligible[i]:
            bit = 1 << pos
            if used & bit:
                continue
            assignment.append((hid, pos))
            rec(i + 1, used | bit, score + pos_vectors[i][pos - 1], assignment)
            assignment.pop()

    rec(0, 0, 0.0, [])
    if best_assign is None:
        return None
    return best_score, best_assign


def best_assignment(
    hero_ids: list[int] | tuple[int, ...], positions: dict[int, list[float]]
) -> tuple[float, dict[int, int]] | None:
    """Best distinct-position assignment for up to 5 heroes.

    Returns (sum of position probabilities, {hero_id: position}) or None
    when no assignment respecting the minimum position probability exists.
    """
    if not hero_ids:
        return 0.0, {}
    if len(hero_ids) > 5:
        raise ValueError("a Dota lineup has at most 5 heroes")

    ids = tuple(hero_ids)
    vectors = tuple(tuple(positions.get(hid, [0.2] * 5)) for hid in ids)
    result = _cached_best_assignment(ids, vectors)
    if result is None:
        return None
    score, assignment = result
    return score, dict(assignment)


def can_finish_roster_shape(
    core_only: int,
    support_only: int,
    flex: int,
    remaining_picks: int,
    pool_core_only: int,
    pool_support_only: int,
    pool_flex: int,
) -> bool:
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
        for s_future in range(
            0, min(pool_support_only, remaining_picks - c_future) + 1
        ):
            f_future = remaining_picks - c_future - s_future
            if f_future > pool_flex:
                continue
            c_total = core_only + c_future
            s_total = support_only + s_future
            f_total = flex + f_future
            if c_total <= 3 and s_total <= 2 and c_total + s_total + f_total == 5:
                return True
    return False
