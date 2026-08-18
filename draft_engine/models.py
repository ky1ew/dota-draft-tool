"""Core draft state machine.

The draft order is intentionally data-driven because Valve changes
Captain's Mode every few patches.  The sequence below is the official
post-7.40 order (used by current simulators):

  Ban phase 1:  F F S S F S S
  Pick phase 1: F S
  Ban phase 2:  F F S
  Pick phase 2: S F F S S F
  Ban phase 3:  F S F S
  Pick phase 3: F S

where F = team with the first pick, S = team with the second pick.
The Radiant/Dire mapping is a separate setting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

TEAM_A = "a"  # first-pick team
TEAM_B = "b"  # second-pick team


@dataclass(frozen=True)
class Turn:
    action: str  # "ban" | "pick"
    team: str    # TEAM_A / TEAM_B
    phase: int   # 1..3


# Official current Captain's Mode order (post 7.40).  24 steps total:
# 14 bans (7 per team) and 10 picks (5 per team).
DRAFT_ORDER_740: tuple[Turn, ...] = tuple(
    Turn(*t) for t in [
        ("ban", "a", 1), ("ban", "a", 1), ("ban", "b", 1), ("ban", "b", 1),
        ("ban", "a", 1), ("ban", "b", 1), ("ban", "b", 1),
        ("pick", "a", 1), ("pick", "b", 1),
        ("ban", "a", 2), ("ban", "a", 2), ("ban", "b", 2),
        ("pick", "b", 2), ("pick", "a", 2), ("pick", "a", 2),
        ("pick", "b", 2), ("pick", "b", 2), ("pick", "a", 2),
        ("ban", "a", 3), ("ban", "b", 3), ("ban", "a", 3), ("ban", "b", 3),
        ("pick", "a", 3), ("pick", "b", 3),
    ]
)

# Pre-7.40 / 7.34 order, kept to demonstrate configurable order support.
DRAFT_ORDER_734: tuple[Turn, ...] = tuple(
    Turn(*t) for t in [
        ("ban", "a", 1), ("ban", "b", 1), ("ban", "b", 1), ("ban", "a", 1),
        ("ban", "b", 1), ("ban", "b", 1), ("ban", "a", 1),
        ("pick", "a", 1), ("pick", "b", 1),
        ("ban", "a", 2), ("ban", "a", 2), ("ban", "b", 2),
        ("pick", "b", 2), ("pick", "a", 2), ("pick", "a", 2),
        ("pick", "b", 2), ("pick", "b", 2), ("pick", "a", 2),
        ("ban", "a", 3), ("ban", "b", 3), ("ban", "b", 3), ("ban", "a", 3),
        ("pick", "a", 3), ("pick", "b", 3),
    ]
)

DRAFT_ORDERS = {"7.40": DRAFT_ORDER_740, "7.34": DRAFT_ORDER_734}


@dataclass
class Hero:
    id: int
    name: str
    localized_name: str
    primary_attr: str
    attack_type: str
    roles: tuple[str, ...]
    cm_enabled: bool = True
    legs: int = 0

    @property
    def display_name(self) -> str:
        return self.localized_name or self.name

    def __hash__(self) -> int:
        return self.id

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"{self.display_name} (id={self.id})"


@dataclass
class HeroStats:
    id: int
    localized_name: str
    cm_enabled: bool
    pub_pick: int = 0
    pub_win: int = 0
    pro_pick: int = 0
    pro_win: int = 0
    pro_ban: int = 0


@dataclass
class DraftState:
    heroes: dict[int, Hero]
    hero_stats: dict[int, HeroStats]
    order: tuple[Turn, ...] = DRAFT_ORDER_740
    first_pick_side: str = "radiant"  # "radiant" | "dire"
    index: int = 0
    radiant_picks: list[int] = field(default_factory=list)
    dire_picks: list[int] = field(default_factory=list)
    radiant_bans: list[int] = field(default_factory=list)
    dire_bans: list[int] = field(default_factory=list)

    # ----- side helpers -------------------------------------------------
    def team_side(self, team_key: str) -> str:
        if team_key == TEAM_A:
            return self.first_pick_side
        return "dire" if self.first_pick_side == "radiant" else "radiant"

    def side_picks(self, side: str) -> list[int]:
        return self.radiant_picks if side == "radiant" else self.dire_picks

    def side_bans(self, side: str) -> list[int]:
        return self.radiant_bans if side == "radiant" else self.dire_bans

    def team_picks(self, team_key: str) -> list[int]:
        return self.side_picks(self.team_side(team_key))

    def team_bans(self, team_key: str) -> list[int]:
        return self.side_bans(self.team_side(team_key))

    # ----- state queries -------------------------------------------------
    @property
    def done(self) -> bool:
        return self.index >= len(self.order)

    @property
    def current_turn(self) -> Turn | None:
        return None if self.done else self.order[self.index]

    @property
    def used_heroes(self) -> set[int]:
        return set(
            self.radiant_picks + self.dire_picks
            + self.radiant_bans + self.dire_bans
        )

    def legal_heroes(self) -> list[Hero]:
        used = self.used_heroes
        return [
            h for h in self.heroes.values()
            if h.id not in used and h.cm_enabled
        ]

    def hero_name(self, hid: int) -> str:
        return self.heroes[hid].display_name if hid in self.heroes else f"Hero {hid}"

    # ----- mutations ------------------------------------------------------
    def apply_current(self, hero_id: int, expected_action: str | None = None) -> None:
        """Apply a hero to the current turn (raises on invalid move).

        If expected_action is supplied (e.g. from a `pick` or `ban` command)
        the move is rejected when the draft is currently in the other phase.
        """
        turn = self.current_turn
        if turn is None:
            raise ValueError("draft is already complete")
        if expected_action is not None and expected_action != turn.action:
            raise ValueError(
                f"current turn is a {turn.action}, not a {expected_action}"
            )
        hero = self.heroes.get(hero_id)
        if hero is None:
            raise ValueError(f"unknown hero id: {hero_id}")
        if not hero.cm_enabled:
            raise ValueError(f"{hero.display_name} is not enabled in Captain's Mode")
        if hero_id in self.used_heroes:
            raise ValueError(f"{hero.display_name} is already picked or banned")

        side = self.team_side(turn.team)
        target = self.side_picks(side) if turn.action == "pick" else self.side_bans(side)
        target.append(hero_id)
        self.index += 1

    def undo(self) -> None:
        if self.index == 0:
            return
        self.index -= 1
        turn = self.order[self.index]
        side = self.team_side(turn.team)
        target = self.side_picks(side) if turn.action == "pick" else self.side_bans(side)
        if target:
            target.pop()

    def reset(self) -> None:
        self.index = 0
        self.radiant_picks = []
        self.dire_picks = []
        self.radiant_bans = []
        self.dire_bans = []

    # ----- formatting -------------------------------------------------------
    def format_lineup(self, side: str) -> str:
        picks = [self.hero_name(h) for h in self.side_picks(side)]
        bans = [self.hero_name(h) for h in self.side_bans(side)]
        return f"{side.title():7s} picks: {', '.join(picks) if picks else '—':45s} bans: {', '.join(bans) if bans else '—'}"

    def format_board(self) -> str:
        turn = self.current_turn
        header = "DRAFT COMPLETE" if turn is None else (
            f"Step {self.index + 1}/{len(self.order)} — "
            f"{self.team_side(turn.team).upper()} "
            f"{turn.action.upper()} (phase {turn.phase})"
        )
        return "\n".join([
            header,
            self.format_lineup("radiant"),
            self.format_lineup("dire"),
        ])


def hero_from_dict(row: dict) -> Hero:
    return Hero(
        id=int(row["id"]),
        name=row.get("name", ""),
        localized_name=row.get("localized_name", ""),
        primary_attr=row.get("primary_attr", "str"),
        attack_type=row.get("attack_type", "Melee"),
        roles=tuple(row.get("roles") or []),
        cm_enabled=bool(row.get("cm_enabled", True)),
        legs=int(row.get("legs", 0)),
    )


def stats_from_dict(row: dict) -> HeroStats:
    return HeroStats(
        id=int(row["id"]),
        localized_name=row.get("localized_name", ""),
        cm_enabled=bool(row.get("cm_enabled", True)),
        pub_pick=int(row.get("pub_pick", 0) or 0),
        pub_win=int(row.get("pub_win", 0) or 0),
        pro_pick=int(row.get("pro_pick", 0) or 0),
        pro_win=int(row.get("pro_win", 0) or 0),
        pro_ban=int(row.get("pro_ban", 0) or 0),
    )


def build_state(hero_rows: Iterable[dict], stats_rows: Iterable[dict],
                first_pick_side: str = "radiant",
                order: tuple[Turn, ...] = DRAFT_ORDER_740) -> DraftState:
    hero_stats = {int(s["id"]): stats_from_dict(s) for s in stats_rows}
    heroes: dict[int, Hero] = {}
    for h in hero_rows:
        hero = hero_from_dict(h)
        stats = hero_stats.get(hero.id)
        if stats is not None:
            hero.cm_enabled = stats.cm_enabled
        heroes[hero.id] = hero
    if first_pick_side not in ("radiant", "dire"):
        raise ValueError("first_pick_side must be 'radiant' or 'dire'")
    return DraftState(heroes=heroes, hero_stats=hero_stats,
                      order=order, first_pick_side=first_pick_side)
