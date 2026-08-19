"""Same-team pair synergy model.

Counter data only says how hero A performs *against* hero B.  Drafting
also needs the opposite question: how do A and B perform **together**?

We build that matrix from recent professional matches:

  * fetch /proMatches for recent pro match ids
  * parse each /matches/{id} to get the 10 picked heroes + winner
  * every unordered hero pair on the winning team gets a win
  * every unordered pair on the losing team gets a loss

Samples are small, so scoring applies the same Bayesian shrinkage used for
counter matchups and only surfaces reasons when the sample is meaningful.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from pathlib import Path

from .config import DEFAULT_CONFIG
from .data import CACHE_DIR, _http_get_json, _write_json
from .exceptions import DataFetchError

logger = logging.getLogger(__name__)

MATCH_CACHE = CACHE_DIR / "synergy_matches.json"
MIN_PAIR_GAMES = 10


def _heroes_for_side(players: list[dict], radiant: bool) -> list[int]:
    out = []
    for p in players:
        slot = int(p.get("player_slot", 0))
        is_radiant = slot < 128
        hid = p.get("hero_id")
        if hid is not None and is_radiant == radiant:
            out.append(int(hid))
    return out


def fetch_synergy_data(limit: int = 100) -> dict[int, dict]:
    """Download and cache parsed pro matches used for synergy training."""
    existing: dict[int, dict] = {}
    if MATCH_CACHE.exists():
        existing = {
            int(k): v
            for k, v in json.loads(MATCH_CACHE.read_text(encoding="utf-8")).items()
        }

    logger.info("Fetching recent pro match list...")
    pro_matches = _http_get_json(f"{DEFAULT_CONFIG.data.base_url}/proMatches")
    todo = [m for m in pro_matches if int(m["match_id"]) not in existing][:limit]

    for i, match in enumerate(todo, 1):
        mid = int(match["match_id"])
        logger.info("Parsing pro match %d/%d: %s", i, len(todo), mid)
        try:
            parsed = _http_get_json(f"{DEFAULT_CONFIG.data.base_url}/matches/{mid}")
        except DataFetchError:
            logger.exception("could not parse match %s", mid)
            continue
        players = parsed.get("players") or []
        if len(players) != 10:
            continue
        existing[mid] = {
            "radiant_win": bool(parsed.get("radiant_win", False)),
            "radiant_heroes": _heroes_for_side(players, True),
            "dire_heroes": _heroes_for_side(players, False),
        }
        time.sleep(DEFAULT_CONFIG.data.request_delay)

    _write_json(MATCH_CACHE, {str(k): v for k, v in existing.items()})
    return existing


def build_synergy_matrix(
    matches: dict[int, dict],
) -> dict[int, dict[int, tuple[int, int]]]:
    """Return hero_id -> {ally_id: (wins, games)} for same-team pairs."""
    matrix: dict[int, dict[int, tuple[int, int]]] = {}

    def add_pair(a: int, b: int, won: bool) -> None:
        if a == b:
            return
        key = (min(a, b), max(a, b))
        row = matrix.setdefault(key[0], {})
        wins, games = row.get(key[1], (0, 0))
        row[key[1]] = (wins + (1 if won else 0), games + 1)

    for match in matches.values():
        radiant = match.get("radiant_heroes") or []
        dire = match.get("dire_heroes") or []
        radiant_win = bool(match.get("radiant_win", False))
        for a, b in itertools.combinations(radiant, 2):
            add_pair(a, b, radiant_win)
        for a, b in itertools.combinations(dire, 2):
            add_pair(a, b, not radiant_win)
    return matrix


def load_synergy_matrix(
    auto_fetch: bool = False,
) -> dict[int, dict[int, tuple[int, int]]]:
    if not MATCH_CACHE.exists():
        if not auto_fetch:
            logger.info("no synergy cache present; skipping synergy scoring")
            return {}
        fetch_synergy_data()
    raw = json.loads(MATCH_CACHE.read_text(encoding="utf-8"))
    matches = {int(k): v for k, v in raw.items()}
    return build_synergy_matrix(matches)
