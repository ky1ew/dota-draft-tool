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

``DraftState`` is immutable: every mutation returns a new instance.  This
makes undo, caching and future lookahead search safe without deep copies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Literal

from .exceptions import InvalidMoveError

TEAM_A = "a"  # first-pick team
TEAM_B = "b"  # second-pick team

Side = Literal["radiant", "dire"]
TeamKey = Literal["a", "b"]
Action = Literal["ban", "pick"]


@dataclass(frozen=True)
class Turn:
    action: Action
    team: TeamKey
    phase: int  # 1..3


_DRAFT_ORDER_740_RAW: list[tuple[Action, TeamKey, int]] = [
    ("ban", "a", 1),
    ("ban", "a", 1),
    ("ban", "b", 1),
    ("ban", "b", 1),
    ("ban", "a", 1),
    ("ban", "b", 1),
    ("ban", "b", 1),
    ("pick", "a", 1),
    ("pick", "b", 1),
    ("ban", "a", 2),
    ("ban", "a", 2),
    ("ban", "b", 2),
    ("pick", "b", 2),
    ("pick", "a", 2),
    ("pick", "a", 2),
    ("pick", "b", 2),
    ("pick", "b", 2),
    ("pick", "a", 2),
    ("ban", "a", 3),
    ("ban", "b", 3),
    ("ban", "a", 3),
    ("ban", "b", 3),
    ("pick", "a", 3),
    ("pick", "b", 3),
]
DRAFT_ORDER_740: tuple[Turn, ...] = tuple(
    Turn(action, team, phase) for action, team, phase in _DRAFT_ORDER_740_RAW
)

_DRAFT_ORDER_734_RAW: list[tuple[Action, TeamKey, int]] = [
    ("ban", "a", 1),
    ("ban", "b", 1),
    ("ban", "b", 1),
    ("ban", "a", 1),
    ("ban", "b", 1),
    ("ban", "b", 1),
    ("ban", "a", 1),
    ("pick", "a", 1),
    ("pick", "b", 1),
    ("ban", "a", 2),
    ("ban", "a", 2),
    ("ban", "b", 2),
    ("pick", "b", 2),
    ("pick", "a", 2),
    ("pick", "a", 2),
    ("pick", "b", 2),
    ("pick", "b", 2),
    ("pick", "a", 2),
    ("ban", "a", 3),
    ("ban", "b", 3),
    ("ban", "b", 3),
    ("ban", "a", 3),
    ("pick", "a", 3),
    ("pick", "b", 3),
]
DRAFT_ORDER_734: tuple[Turn, ...] = tuple(
    Turn(action, team, phase) for action, team, phase in _DRAFT_ORDER_734_RAW
)

DRAFT_ORDERS: dict[str, tuple[Turn, ...]] = {
    "7.40": DRAFT_ORDER_740,
    "7.34": DRAFT_ORDER_734,
}


@dataclass(frozen=True)
class Hero:
    """Immutable hero metadata.

    Frozen + explicit id hash makes Hero safe to use as a dict key or in
    sets (a mutable hero with a field hash was previously unsafe).
    """

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


@dataclass(frozen=True)
class HeroStats:
    id: int
    localized_name: str
    cm_enabled: bool
    pub_pick: int = 0
    pub_win: int = 0
    pro_pick: int = 0
    pro_win: int = 0
    pro_ban: int = 0


@dataclass(frozen=True, eq=False)
class DraftState:
    """Immutable Captain's Mode draft state.

    All mutation methods return a new DraftState; the original is never
    modified, which makes branch simulation and undo trivially safe.
    """

    heroes: dict[int, Hero]
    hero_stats: dict[int, HeroStats]
    order: tuple[Turn, ...] = DRAFT_ORDER_740
    first_pick_side: Side = "radiant"
    index: int = 0
    radiant_picks: tuple[int, ...] = ()
    dire_picks: tuple[int, ...] = ()
    radiant_bans: tuple[int, ...] = ()
    dire_bans: tuple[int, ...] = ()

    # ----- side helpers -------------------------------------------------
    def team_side(self, team_key: TeamKey) -> Side:
        if team_key == TEAM_A:
            return self.first_pick_side
        return "dire" if self.first_pick_side == "radiant" else "radiant"

    def side_picks(self, side: Side) -> tuple[int, ...]:
        return self.radiant_picks if side == "radiant" else self.dire_picks

    def side_bans(self, side: Side) -> tuple[int, ...]:
        return self.radiant_bans if side == "radiant" else self.dire_bans

    def team_picks(self, team_key: TeamKey) -> tuple[int, ...]:
        return self.side_picks(self.team_side(team_key))

    def team_bans(self, team_key: TeamKey) -> tuple[int, ...]:
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
            self.radiant_picks + self.dire_picks + self.radiant_bans + self.dire_bans
        )

    def legal_heroes(self) -> list[Hero]:
        used = self.used_heroes
        return [h for h in self.heroes.values() if h.id not in used and h.cm_enabled]

    def hero_name(self, hid: int) -> str:
        return self.heroes[hid].display_name if hid in self.heroes else f"Hero {hid}"

    # ----- mutations (return new states) ---------------------------------
    def apply_current(
        self, hero_id: int, expected_action: Action | None = None
    ) -> "DraftState":
        """Return a new state with hero applied to the current turn.

        If expected_action is supplied (e.g. from a `pick` or `ban` command)
        the move is rejected when the draft is currently in the other phase.
        """
        turn = self.current_turn
        if turn is None:
            raise InvalidMoveError("draft is already complete")
        if expected_action is not None and expected_action != turn.action:
            raise InvalidMoveError(
                f"current turn is a {turn.action}, not a {expected_action}"
            )
        hero = self.heroes.get(hero_id)
        if hero is None:
            raise InvalidMoveError(f"unknown hero id: {hero_id}")
        if not hero.cm_enabled:
            raise InvalidMoveError(
                f"{hero.display_name} is not enabled in Captain's Mode"
            )
        if hero_id in self.used_heroes:
            raise InvalidMoveError(f"{hero.display_name} is already picked or banned")

        side = self.team_side(turn.team)
        new_index = self.index + 1
        if turn.action == "pick":
            if side == "radiant":
                return replace(
                    self, index=new_index, radiant_picks=self.radiant_picks + (hero_id,)
                )
            return replace(
                self, index=new_index, dire_picks=self.dire_picks + (hero_id,)
            )
        if side == "radiant":
            return replace(
                self, index=new_index, radiant_bans=self.radiant_bans + (hero_id,)
            )
        return replace(self, index=new_index, dire_bans=self.dire_bans + (hero_id,))

    def undo(self) -> "DraftState":
        """Return a new state with the previous move removed."""
        if self.index == 0:
            return self
        prev = self.order[self.index - 1]
        side = self.team_side(prev.team)
        new_index = self.index - 1
        if prev.action == "pick":
            if side == "radiant":
                return replace(
                    self, index=new_index, radiant_picks=self.radiant_picks[:-1]
                )
            return replace(self, index=new_index, dire_picks=self.dire_picks[:-1])
        if side == "radiant":
            return replace(self, index=new_index, radiant_bans=self.radiant_bans[:-1])
        return replace(self, index=new_index, dire_bans=self.dire_bans[:-1])

    def reset(self) -> "DraftState":
        return replace(
            self,
            index=0,
            radiant_picks=(),
            dire_picks=(),
            radiant_bans=(),
            dire_bans=(),
        )

    # ----- formatting -------------------------------------------------------
    def format_lineup(self, side: Side) -> str:
        picks = [self.hero_name(h) for h in self.side_picks(side)]
        bans = [self.hero_name(h) for h in self.side_bans(side)]
        return (
            f"{side.title():7s} picks: "
            f"{', '.join(picks) if picks else '—':45s} "
            f"bans: {', '.join(bans) if bans else '—'}"
        )

    def format_board(self) -> str:
        turn = self.current_turn
        header = (
            "DRAFT COMPLETE"
            if turn is None
            else (
                f"Step {self.index + 1}/{len(self.order)} — "
                f"{self.team_side(turn.team).upper()} "
                f"{turn.action.upper()} (phase {turn.phase})"
            )
        )
        return "\n".join(
            [
                header,
                self.format_lineup("radiant"),
                self.format_lineup("dire"),
            ]
        )


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


def build_state(
    hero_rows: Iterable[dict],
    stats_rows: Iterable[dict],
    first_pick_side: Side = "radiant",
    order: tuple[Turn, ...] = DRAFT_ORDER_740,
) -> DraftState:
    hero_stats = {int(s["id"]): stats_from_dict(s) for s in stats_rows}
    heroes: dict[int, Hero] = {}
    for h in hero_rows:
        hero = hero_from_dict(h)
        stats = hero_stats.get(hero.id)
        if stats is not None:
            # /heroes has no cm_enabled field; /heroStats does.
            hero = replace(hero, cm_enabled=stats.cm_enabled)
        heroes[hero.id] = hero
    if first_pick_side not in ("radiant", "dire"):
        raise ValueError("first_pick_side must be 'radiant' or 'dire'")
    return DraftState(
        heroes=heroes,
        hero_stats=hero_stats,
        order=order,
        first_pick_side=first_pick_side,
    )
