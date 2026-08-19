"""Command-line interface and demo for the Dota 2 draft prototype."""

from __future__ import annotations

import argparse
import logging
import sys

from .data import load_heroes, load_hero_stats, load_matchups
from .exceptions import DraftEngineError
from .logging_config import setup_logging
from .models import DRAFT_ORDERS, DRAFT_ORDER_740, DraftState, Side
from .roles import fetch_position_roles
from .scoring import DraftAdvisor, Suggestion
from .synergy import MATCH_CACHE, fetch_synergy_data, load_synergy_matrix

logger = logging.getLogger(__name__)


def print_suggestions(title: str, suggestions: list[Suggestion]) -> None:
    print(f"\n{title}")
    for i, s in enumerate(suggestions, 1):
        reasons = "; ".join(s.reasons)
        print(
            f"  {i:>2}. {s.hero.display_name:<24s} score {s.score:7.2f}  |  {reasons}"
        )


def resolve_hero(state: DraftState, query: str) -> int:
    from .exceptions import HeroNotFoundError

    q = query.strip().lower()
    if not q:
        raise HeroNotFoundError(query)
    if q.isdigit():
        hid = int(q)
        if hid in state.heroes:
            return hid
        raise HeroNotFoundError(query)
    exact = [h for h in state.heroes.values() if h.display_name.lower() == q]
    if len(exact) == 1:
        return exact[0].id
    partial = [h for h in state.heroes.values() if q in h.display_name.lower()]
    if len(partial) == 1:
        return partial[0].id
    if not partial:
        raise HeroNotFoundError(query)
    names = ", ".join(h.display_name for h in partial[:12])
    raise HeroNotFoundError(f"'{query}' (matches: {names})")


def build_advisor(args) -> tuple[DraftState, DraftAdvisor]:
    logger.info("Loading hero data...")
    print("Loading hero data...")
    heroes = load_heroes(refresh=args.refresh)
    stats = load_hero_stats(refresh=args.refresh)
    matchups = load_matchups(refresh=args.refresh)
    if args.refresh:
        fetch_position_roles()

    synergy = {}
    if args.refresh_synergy:
        fetch_synergy_data()
        synergy = load_synergy_matrix(auto_fetch=False)
    elif args.synergy or MATCH_CACHE.exists():
        synergy = load_synergy_matrix(auto_fetch=args.synergy)

    from .models import build_state

    order = DRAFT_ORDERS.get(args.order, DRAFT_ORDER_740)
    state = build_state(heroes, stats, first_pick_side=args.side, order=order)
    advisor = DraftAdvisor(state, matchups, synergy=synergy)
    cm_heroes = sum(1 for h in state.heroes.values() if h.cm_enabled)
    logger.info(
        "Loaded %s Captain's Mode heroes, %s matchup rows, %s synergy pairs.",
        cm_heroes,
        len(matchups),
        sum(len(v) for v in synergy.values()),
    )
    print(
        f"Loaded {cm_heroes} Captain's Mode heroes, "
        f"{len(matchups)} matchup rows, "
        f"{sum(len(v) for v in synergy.values())} synergy pairs."
    )
    return state, advisor


def demo(args) -> int:
    state, advisor = build_advisor(args)
    print(
        f"\n=== Simulated Captain's Mode draft (order {args.order}, "
        f"first pick {state.first_pick_side.upper()}) ===\n"
    )

    while not state.done:
        turn = state.current_turn
        assert turn is not None
        side = state.team_side(turn.team)
        suggestions = (
            advisor.suggest_bans(side, args.limit)
            if turn.action == "ban"
            else advisor.suggest_picks(side, args.limit)
        )
        if not suggestions:
            logger.error("no legal suggestions at step %s", state.index + 1)
            return 2
        chosen = suggestions[0]

        print(
            f"Step {state.index + 1:>2}/{len(state.order)}  "
            f"{side.upper():7s} {turn.action.upper():4s} "
            f"(phase {turn.phase})  ->  {chosen.hero.display_name}"
        )
        print(f"    reason: {'; '.join(chosen.reasons)}")
        print(
            f"    other options: "
            + ", ".join(s.hero.display_name for s in suggestions[1:5])
        )
        print()
        state = state.apply_current(chosen.hero.id, expected_action=turn.action)
        advisor.bind(state)

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
        suggestions = (
            advisor.suggest_bans(side, args.limit)
            if turn.action == "ban"
            else advisor.suggest_picks(side, args.limit)
        )
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
                print(
                    "Commands: pick/ban <hero>, suggest, auto, undo, board, reset, quit"
                )
            elif cmd in ("s", "suggest"):
                show_suggestions()
            elif cmd in ("b", "board"):
                print("\n" + state.format_board())
                for side in ("radiant", "dire"):
                    if len(state.side_picks(side)) == 5:
                        print(advisor.describe_lineup(side))
                show_suggestions()
            elif cmd in ("u", "undo"):
                state = state.undo()
                advisor.bind(state)
                print("\n" + state.format_board())
                show_suggestions()
            elif cmd == "reset":
                state = state.reset()
                advisor.bind(state)
                print("\nDraft reset.\n" + state.format_board())
                show_suggestions()
            elif cmd in ("p", "pick"):
                state = state.apply_current(
                    resolve_hero(state, arg), expected_action="pick"
                )
                advisor.bind(state)
                print("OK\n" + state.format_board())
                side = state.team_side(state.order[state.index - 1].team)
                if len(state.side_picks(side)) == 5:
                    print(advisor.describe_lineup(side))
                show_suggestions()
            elif cmd == "ban":
                state = state.apply_current(
                    resolve_hero(state, arg), expected_action="ban"
                )
                advisor.bind(state)
                print("OK\n" + state.format_board())
                show_suggestions()
            elif cmd in ("a", "auto"):
                turn = state.current_turn
                if turn is None:
                    print("Draft already complete.")
                    continue
                side = state.team_side(turn.team)
                suggestion = (
                    advisor.best_ban(side)
                    if turn.action == "ban"
                    else advisor.best_pick(side)
                )
                state = state.apply_current(
                    suggestion.hero.id, expected_action=turn.action
                )
                advisor.bind(state)
                print(
                    f"Auto {turn.action}: {suggestion.hero.display_name} "
                    f"({' ; '.join(suggestion.reasons)})\n"
                )
                show_suggestions()
            else:
                print(f"unknown command: {cmd}")
        except DraftEngineError as e:
            print(f"error: {e}")
        except ValueError as e:
            print(f"error: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dota 2 Captain's Mode draft assistant prototype"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="simulate a full draft with greedy suggestions",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-download OpenDota data"
    )
    parser.add_argument(
        "--side",
        choices=("radiant", "dire"),
        default="radiant",
        help="team with the first pick (default: radiant)",
    )
    parser.add_argument(
        "--order",
        choices=("7.40", "7.34"),
        default="7.40",
        help="Captain's Mode draft order version",
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="number of suggestions to show"
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging verbosity",
    )
    parser.add_argument(
        "--synergy",
        action="store_true",
        help="use same-team pair synergy data (downloads if missing)",
    )
    parser.add_argument(
        "--refresh-synergy",
        action="store_true",
        help="re-download pro matches and rebuild synergy matrix",
    )
    args = parser.parse_args(argv)

    setup_logging(level=getattr(logging, args.log_level))
    if args.demo:
        return demo(args)
    return interactive(args)


if __name__ == "__main__":
    raise SystemExit(main())
