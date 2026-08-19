"""Data loading and caching for the Dota 2 draft prototype.

All data comes from the free OpenDota API:
  /api/heroes                 - hero metadata
  /api/heroStats              - current patch stats, cm_enabled, pro data
  /api/heroes/{id}/matchups   - pairwise winrate of hero vs every hero

Everything is cached to disk so repeated runs are instant and we respect
OpenDota's rate limit (~60 requests/minute).

Matchups are consolidated into a single cache/matchups.json file instead
of 127 tiny files, giving a ~10x reduction in cache I/O.  A migration from
the old cache/matchups/{id}.json layout happens automatically.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG
from .exceptions import DataFetchError

logger = logging.getLogger(__name__)

BASE_URL = DEFAULT_CONFIG.data.base_url
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
OLD_MATCHUP_DIR = CACHE_DIR / "matchups"
MATCHUP_FILE = CACHE_DIR / "matchups.json"
HEADERS = {"User-Agent": "dota-draft-tool/0.2 (prototype)"}
REQUEST_DELAY = DEFAULT_CONFIG.data.request_delay


def _http_get_json(url: str, timeout: int | None = None) -> Any:
    """GET a JSON URL with basic retry/backoff for rate limits."""
    timeout = timeout or DEFAULT_CONFIG.data.timeout
    last_err: Exception | None = None
    for attempt in range(5):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = 20 * (attempt + 1)
                logger.warning("rate limited (HTTP 429), waiting %ss", wait)
                time.sleep(wait)
                continue
            if e.code in (404, 422):
                break
        except Exception as e:  # network hiccup
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise DataFetchError(url, str(last_err))


def _read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise DataFetchError(str(path), str(e))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    tmp.replace(path)


def load_heroes(refresh: bool = False) -> list[dict]:
    path = CACHE_DIR / "heroes.json"
    if refresh or not path.exists():
        logger.info("Downloading hero metadata from OpenDota...")
        data = _http_get_json(f"{BASE_URL}/heroes")
        _write_json(path, data)
    return _read_json(path)


def load_hero_stats(refresh: bool = False) -> list[dict]:
    path = CACHE_DIR / "hero_stats.json"
    if refresh or not path.exists():
        logger.info("Downloading heroStats from OpenDota...")
        data = _http_get_json(f"{BASE_URL}/heroStats")
        _write_json(path, data)
    return _read_json(path)


def _migrate_old_matchups() -> bool:
    """Build consolidated matchups.json from the legacy per-hero files."""
    if MATCHUP_FILE.exists() or not OLD_MATCHUP_DIR.exists():
        return False
    matrix: dict[int, dict[int, dict[str, int]]] = {}
    for file in sorted(OLD_MATCHUP_DIR.glob("*.json")):
        try:
            rows = _read_json(file)
        except DataFetchError:
            logger.warning("skipping unreadable legacy matchup file %s", file)
            continue
        hero_id = int(file.stem)
        matrix[hero_id] = {
            int(row["hero_id"]): {
                "wins": int(row["wins"]),
                "games": int(row["games_played"]),
            }
            for row in rows
        }
    if matrix:
        _write_json(MATCHUP_FILE, matrix)
        logger.info(
            "migrated %s legacy matchup files into %s", len(matrix), MATCHUP_FILE.name
        )
        return True
    return False


def _fetch_matchups_consolidated() -> dict[int, dict[int, dict[str, int]]]:
    heroes = [h for h in load_heroes() if h.get("id")]
    matrix: dict[int, dict[int, dict[str, int]]] = {}
    for i, hero in enumerate(heroes, 1):
        hid = int(hero["id"])
        logger.info(
            "Fetching matchups %d/%d: %s",
            i,
            len(heroes),
            hero.get("localized_name", hid),
        )
        url = f"{BASE_URL}/heroes/{hid}/matchups"
        try:
            rows = _http_get_json(url)
        except DataFetchError:
            logger.exception("failed to fetch matchups for hero %s", hid)
            rows = []
        matrix[hid] = {
            int(row["hero_id"]): {
                "wins": int(row["wins"]),
                "games": int(row["games_played"]),
            }
            for row in rows
        }
        time.sleep(REQUEST_DELAY)
    _write_json(MATCHUP_FILE, matrix)
    return matrix


def load_matchups(refresh: bool = False) -> dict[int, dict[int, dict[str, int]]]:
    """Return hero_id -> {opponent_id: {'wins', 'games'}} from one file.

    On first run after an upgrade, the legacy per-hero cache is migrated
    automatically.  With `refresh=True` the matrix is re-downloaded from
    OpenDota (~2 minutes, rate limited).
    """
    _migrate_old_matchups()
    if refresh and MATCHUP_FILE.exists():
        MATCHUP_FILE.unlink()

    if not MATCHUP_FILE.exists():
        logger.info("Building consolidated matchup matrix...")
        matrix = _fetch_matchups_consolidated()
    else:
        matrix = _read_json(MATCHUP_FILE)

    # JSON object keys are strings; convert once to ints for O(1) lookups.
    return {
        int(hid): {int(oid): row for oid, row in opponents.items()}
        for hid, opponents in matrix.items()
    }


def warm_cache() -> None:
    """Download everything needed by the prototype."""
    load_heroes()
    load_hero_stats()
    load_matchups()
    from .roles import fetch_position_roles  # local import avoids a cycle

    fetch_position_roles()
