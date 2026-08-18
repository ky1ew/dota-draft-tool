"""Command-line interface and demo for the Dota 2 draft prototype."""
from __future__ import annotations

import argparse
import sys

from .data import load_heroes, load_hero_stats, load_matchups
from .models import DRAFT_ORDERS, DRAFT_ORDER_740, DraftState, build_state
from .roles import fetch_position_roles
from .scoring import DraftAdvisor, Suggestion


def print_suggestions(title: str, suggestions: list[Suggestion]) -> None:
    print(f"\n{title}")
    for i, s in enumerate(suggestions, 1):
        reasons = "; ".join(s.reasons)
        print(f"  {i:>2}. {s.hero.display_name:<24s} score {s.score:7.2f}  |  {reasons}")


def resolve_hero(state: DraftState, query: str) -> int:
    q = query.strip().lower()
    if not q:
        raise ValueError("provide a hero name or id")
    if q.isdigit():
        hid = int(q)
        if hid in state.heroes:
            return hid
        raise ValueError(f"unknown hero id {hid}")
    exact = [h for h in state.heroes.values() if h.display_name.lower() == q]
    if len(exact) == 1:
        return exact[0].id
    partial = [h for h in state.heroes.values() if q in h.display_name.lower()]
    if len(partial) == 1:
        return partial[0].id
    if not partial:
        raise ValueError(f"no hero matches '{query}'")
    names = ", ".join(h.display_name for h in partial[:12])
    raise ValueError(f"'{query}' is ambiguous: {names}")


def build_advisor(args) -> tuple[DraftState, DraftAdvisor]:
    print("Loading hero data...")
    heroes = load_heroes(refresh=args.refresh)
    stats = load_hero_stats(refresh=args.refresh)
    matchups = load_matchups(refresh=args.refresh)
    if args.refresh:
        fetch_position_roles()
    order = DRAFT_ORDERS.get(args.order, DRAFT_ORDER_740)
    state = build_state(heroes, stats, first_pick_side=args.side, order=order)
    advisor = DraftAdvisor(state, matchups)
    cm_heroes = sum(1 for h in state.heroes.values() if h.cm_enabled)
    print(f"Loaded {cm_heroes} Captain's Mode heroes and "
          f"{len(matchups)} matchup rows.")
    return state, advisor


def demo(args) -> int:
    state, advisor = build_advisor(args)
    print(f"\n=== Simulated Captain's Mode draft (order {args.order}, "
          f"first pick {state.first_pick_side.upper()}) ===\n")

    while not state.done:
        turn = state.current_turn
        assert turn is not None
        side = state.team_side(turn.team)
        suggestions = (advisor.suggest_bans(side, args.limit)
                       if turn.action == "ban"
                       else advisor.suggest_picks(side, args.limit))
        chosen = suggestions[0]

        print(f"Step {state.index + 1:>2}/{len(state.order)}  "
              f"{side.upper():7s} {turn.action.upper():4s} "
              f"(phase {turn.phase})  ->  {chosen.hero.display_name}")
        print(f"    reason: {'; '.join(chosen.reasons)}")
        print(f"    other options: "
              + ", ".join(s.hero.display_name for s in suggestions[1:5]))
        print()
        state.apply_current(chosen.hero.id)

    print("=== Final draft ===\n" + state.format_board())
    print()
    print(advisor.describe_lineup("radiant"))
    print(advisor.describe_lineup("dire"))
    print()
    return 0


def interactive(args) -> int:
    state, advisor = build_advisor(args)

    def show_suggestions() -> None:
        turn = state.current_turn
        if turn is None:
            return
        side = state.team_side(turn.team)
        suggestions = (advisor.suggest_bans(side, args.limit)
                       if turn.action == "ban"
                       else advisor.suggest_picks(side, args.limit))
        print_suggestions(f"Suggested {turn.action}s for {side.upper()}:", suggestions)

    print("\nCommands: pick/ban <hero>, suggest, auto, undo, board, reset, quit")
    show_suggestions()

    while True:
        try:
            raw = input("\ndraft> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        cmd, _, arg = raw.partition(" ")

        try:
            if cmd in ("q", "quit", "exit"):
                return 0
            elif cmd == "help":
                print("Commands: pick/ban <hero>, suggest, auto, undo, board, reset, quit")
            elif cmd in ("s", "suggest"):
                show_suggestions()
            elif cmd in ("b", "board"):
                print("\n" + state.format_board())
                for side in ("radiant", "dire"):
                    if len(state.side_picks(side)) == 5:
                        print(advisor.describe_lineup(side))
                show_suggestions()
            elif cmd in ("u", "undo"):
                state.undo()
                print("\n" + state.format_board())
                show_suggestions()
            elif cmd == "reset":
                state.reset()
                print("\nDraft reset.\n" + state.format_board())
                show_suggestions()
            elif cmd in ("p", "pick"):
                state.apply_current(resolve_hero(state, arg),
                                    expected_action="pick")
                print("OK\n" + state.format_board())
                side = state.team_side(state.order[state.index - 1].team)
                if len(state.side_picks(side)) == 5:
                    print(advisor.describe_lineup(side))
                show_suggestions()
            elif cmd == "ban":
                state.apply_current(resolve_hero(state, arg),
                                    expected_action="ban")
                print("OK\n" + state.format_board())
                show_suggestions()
            elif cmd in ("a", "auto"):
                turn = state.current_turn
                if turn is None:
                    print("Draft already complete.")
                    continue
                side = state.team_side(turn.team)
                suggestion = (advisor.best_ban(side) if turn.action == "ban"
                              else advisor.best_pick(side))
                state.apply_current(suggestion.hero.id)
                print(f"Auto {turn.action}: {suggestion.hero.display_name} "
                      f"({' ; '.join(suggestion.reasons)})\n")
                show_suggestions()
            else:
                print(f"unknown command: {cmd}")
        except ValueError as e:
            print(f"error: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dota 2 Captain's Mode draft assistant prototype")
    parser.add_argument("--demo", action="store_true",
                        help="simulate a full draft with greedy suggestions")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download OpenDota data")
    parser.add_argument("--side", choices=("radiant", "dire"), default="radiant",
                        help="team with the first pick (default: radiant)")
    parser.add_argument("--order", choices=("7.40", "7.34"), default="7.40",
                        help="Captain's Mode draft order version")
    parser.add_argument("--limit", type=int, default=5,
                        help="number of suggestions to show")
    args = parser.parse_args(argv)
    if args.demo:
        return demo(args)
    return interactive(args)


if __name__ == "__main__":
    raise SystemExit(main())
