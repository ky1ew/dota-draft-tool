"""Data loading and caching for the Dota 2 draft prototype.

All data comes from the free OpenDota API:
  /api/heroes                 - hero metadata
  /api/heroStats              - current patch stats, cm_enabled, pro data
  /api/heroes/{id}/matchups   - pairwise winrate of hero vs every hero

Everything is cached to disk so repeated runs are instant and we respect
OpenDota's rate limit (~60 requests/minute).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://api.opendota.com/api"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
MATCHUP_DIR = CACHE_DIR / "matchups"
HEADERS = {"User-Agent": "dota-draft-tool/0.1 (prototype)"}
REQUEST_DELAY = 1.1  # seconds; keeps us under 60 calls/minute


def _http_get_json(url: str, timeout: int = 30) -> Any:
    """GET a JSON URL with basic retry/backoff for rate limits."""
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
                print(f"  rate limited (HTTP 429), waiting {wait}s...")
                time.sleep(wait)
                continue
            if e.code in (404, 422):
                raise
        except Exception as e:  # network hiccup
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url}: {last_err}")


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    tmp.replace(path)


def load_heroes(refresh: bool = False) -> list[dict]:
    path = CACHE_DIR / "heroes.json"
    if refresh or not path.exists():
        print("Downloading hero metadata from OpenDota...")
        data = _http_get_json(f"{BASE_URL}/heroes")
        _write_json(path, data)
    return _read_json(path)


def load_hero_stats(refresh: bool = False) -> list[dict]:
    path = CACHE_DIR / "hero_stats.json"
    if refresh or not path.exists():
        print("Downloading heroStats from OpenDota...")
        data = _http_get_json(f"{BASE_URL}/heroStats")
        _write_json(path, data)
    return _read_json(path)


def load_matchups(refresh: bool = False, progress: bool = True) -> dict[int, list[dict]]:
    """Load the complete hero-vs-hero matchup matrix.

    One request per hero (~127).  On first run this takes a couple of
    minutes because of rate limiting; after that it is read from cache.
    """
    heroes = load_heroes()
    heroes_cm = [h for h in heroes if h.get("id")]
    result: dict[int, list[dict]] = {}

    for i, hero in enumerate(heroes_cm, 1):
        hid = int(hero["id"])
        path = MATCHUP_DIR / f"{hid}.json"
        if refresh and path.exists():
            path.unlink()
        if path.exists():
            result[hid] = _read_json(path)
            continue

        if progress:
            print(f"Fetching matchups {i}/{len(heroes_cm)}: {hero.get('localized_name', hid)}")
        url = f"{BASE_URL}/heroes/{hid}/matchups"
        try:
            rows = _http_get_json(url)
        except urllib.error.HTTPError:
            rows = []
        _write_json(path, rows)
        result[hid] = rows
        time.sleep(REQUEST_DELAY)
    return result


def warm_cache() -> None:
    """Download everything needed by the prototype."""
    load_heroes()
    load_hero_stats()
    load_matchups()
    from .roles import fetch_position_roles  # local import avoids a cycle
    fetch_position_roles()
