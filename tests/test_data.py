from __future__ import annotations

import json

import pytest

import draft_engine.data as data


def test_write_and_read_json_roundtrip(tmp_path):
    path = tmp_path / "sample.json"
    data._write_json(path, {"a": [1, 2, 3]})
    assert data._read_json(path) == {"a": [1, 2, 3]}


def test_migrate_old_matchups(tmp_path, monkeypatch):
    old_dir = tmp_path / "matchups"
    old_dir.mkdir()
    (old_dir / "1.json").write_text(
        json.dumps([{"hero_id": 2, "wins": 5, "games_played": 10}])
    )
    new_file = tmp_path / "matchups.json"

    monkeypatch.setattr(data, "OLD_MATCHUP_DIR", old_dir)
    monkeypatch.setattr(data, "MATCHUP_FILE", new_file)

    assert data._migrate_old_matchups() is True
    matrix = json.loads(new_file.read_text())
    assert matrix["1"]["2"] == {"wins": 5, "games": 10}


def test_load_matchups_returns_int_keys(tmp_path, monkeypatch):
    old_dir = tmp_path / "no-old-dir"
    new_file = tmp_path / "matchups.json"
    new_file.write_text(json.dumps({"1": {"2": {"wins": 5, "games": 10}}}))

    monkeypatch.setattr(data, "OLD_MATCHUP_DIR", old_dir)
    monkeypatch.setattr(data, "MATCHUP_FILE", new_file)

    matrix = data.load_matchups()
    assert matrix[1][2] == {"wins": 5, "games": 10}


def test_load_heroes_from_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "heroes.json").write_text(
        json.dumps(
            [{"id": 1, "name": "npc_a", "localized_name": "A", "roles": ["Carry"]}]
        )
    )
    monkeypatch.setattr(data, "CACHE_DIR", cache)
    rows = data.load_heroes()
    assert rows[0]["localized_name"] == "A"


def test_load_hero_stats_from_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "hero_stats.json").write_text(
        json.dumps([{"id": 1, "localized_name": "A", "cm_enabled": True}])
    )
    monkeypatch.setattr(data, "CACHE_DIR", cache)
    rows = data.load_hero_stats()
    assert rows[0]["cm_enabled"] is True
