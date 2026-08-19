"""Quick performance benchmark for the consolidated cache and O(1) lookups.

Run from the repository root:  python3 scripts/benchmark.py
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draft_engine.data import load_heroes, load_hero_stats, load_matchups
from draft_engine.models import build_state
from draft_engine.scoring import DraftAdvisor


def timeit(fn, repeat=5):
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main() -> None:
    load_heroes()
    load_hero_stats()
    load_ms = timeit(load_matchups)
    matchups = load_matchups()

    state = build_state(load_heroes(), load_hero_stats())
    build_ms = timeit(lambda: DraftAdvisor(state, matchups))
    advisor = DraftAdvisor(state, matchups)

    ban_ms = timeit(lambda: advisor.suggest_bans("radiant", 5))
    pick_state = state
    for _ in range(7):
        side = pick_state.team_side(pick_state.current_turn.team)
        pick_state = pick_state.apply_current(
            advisor.best_ban(side).hero.id, expected_action="ban"
        )
        advisor.bind(pick_state)
    pick_ms = timeit(lambda: advisor.suggest_picks("radiant", 5))

    print(f"load matchups.json : {load_ms:7.2f} ms")
    print(f"build DraftAdvisor : {build_ms:7.2f} ms")
    print(f"5 ban suggestions  : {ban_ms:7.2f} ms")
    print(f"5 pick suggestions : {pick_ms:7.2f} ms")


if __name__ == "__main__":
    main()
