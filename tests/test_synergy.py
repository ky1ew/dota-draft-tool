from __future__ import annotations

import json

from draft_engine.synergy import build_synergy_matrix


def test_build_synergy_matrix_counts_wins_and_losses():
    matches = {
        1: {
            "radiant_win": True,
            "radiant_heroes": [1, 2, 3, 4, 5],
            "dire_heroes": [6, 7, 8, 9, 10],
        },
        2: {
            "radiant_win": False,
            "radiant_heroes": [1, 2, 3, 4, 5],
            "dire_heroes": [6, 7, 8, 9, 10],
        },
    }
    matrix = build_synergy_matrix(matches)

    # Pair (1, 2) played together twice: one win, one loss.
    assert matrix[1][2] == (1, 2)
    # Symmetric storage uses the smaller id as key.
    assert matrix[2].get(1) is None
    # Pair (6, 7) also played together twice: one win (match 2), one loss.
    assert matrix[6][7] == (1, 2)


def test_build_synergy_matrix_ignores_self_pairs():
    matches = {
        1: {
            "radiant_win": True,
            "radiant_heroes": [1, 1, 2],
            "dire_heroes": [3, 4, 5, 6, 7],
        }
    }
    matrix = build_synergy_matrix(matches)
    assert 1 not in matrix.get(1, {})


def test_heroes_for_side_splits_by_player_slot():
    from draft_engine.synergy import _heroes_for_side

    players = [
        {"player_slot": 0, "hero_id": 1},
        {"player_slot": 4, "hero_id": 2},
        {"player_slot": 128, "hero_id": 3},
        {"player_slot": 132, "hero_id": 4},
    ]
    assert _heroes_for_side(players, True) == [1, 2]
    assert _heroes_for_side(players, False) == [3, 4]


def test_load_synergy_matrix_from_cache(tmp_path, monkeypatch):
    import draft_engine.synergy as synergy

    cache = tmp_path / "synergy_matches.json"
    cache.write_text(
        json.dumps(
            {
                "1": {
                    "radiant_win": True,
                    "radiant_heroes": [1, 2, 3, 4, 5],
                    "dire_heroes": [6, 7, 8, 9, 10],
                }
            }
        )
    )
    monkeypatch.setattr(synergy, "MATCH_CACHE", cache)
    matrix = synergy.load_synergy_matrix(auto_fetch=False)
    assert matrix[1][2] == (1, 1)
