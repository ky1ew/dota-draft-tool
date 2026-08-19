"""Minimal HTTP server + JSON API for the draft visualizer.

No third-party dependencies: uses the standard library HTTP server and
serves the single-page app in web/ plus a small JSON API.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .data import load_heroes, load_hero_stats, load_matchups
from .logging_config import setup_logging
from .models import DRAFT_ORDERS, DRAFT_ORDER_740, DraftState, Side, Turn, build_state
from .roles import fetch_position_roles, role_kind
from .scoring import DraftAdvisor, Suggestion
from .synergy import MATCH_CACHE, fetch_synergy_data, load_synergy_matrix

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class DraftSession:
    """One shared draft session protected by a lock."""

    def __init__(self, args: argparse.Namespace) -> None:
        print("Loading hero data...")
        heroes = load_heroes(refresh=args.refresh)
        stats = load_hero_stats(refresh=args.refresh)
        matchups = load_matchups(refresh=args.refresh)
        if args.refresh:
            fetch_position_roles()

        synergy: dict[int, dict[int, tuple[int, int]]] = {}
        if args.refresh_synergy:
            fetch_synergy_data()
            synergy = load_synergy_matrix(auto_fetch=False)
        elif args.synergy or MATCH_CACHE.exists():
            synergy = load_synergy_matrix(auto_fetch=args.synergy)

        order = DRAFT_ORDERS.get(args.order, DRAFT_ORDER_740)
        self.state = build_state(heroes, stats, first_pick_side=args.side, order=order)
        self.advisor = DraftAdvisor(self.state, matchups, synergy=synergy)
        self.lookahead = args.lookahead
        self.lookahead_depth = args.lookahead_depth
        self.lock = threading.RLock()

        # Raw heroStats rows have image URLs that the parsed model drops.
        self.images: dict[int, str] = {
            int(row["id"]): row.get("img", "") for row in stats
        }
        print(
            f"Loaded {sum(1 for h in self.state.heroes.values() if h.cm_enabled)} "
            f"Captain's Mode heroes, {len(matchups)} matchup rows, "
            f"{sum(len(v) for v in synergy.values())} synergy pairs."
        )

    # ------------------------------------------------------------------
    def _suggestions(self, turn: Turn, limit: int) -> list[Suggestion]:
        from .cli import suggestions_for

        return suggestions_for(
            self.advisor,
            self.state,
            turn,
            limit,
            self.lookahead,
            self.lookahead_depth,
        )

    def _suggestion_items(self, suggestions: list[Suggestion]) -> list[dict]:
        return [
            {
                "id": s.hero.id,
                "name": s.hero.display_name,
                "role": role_kind(self.advisor.position_probs(s.hero.id)),
                "score": s.score,
                "reasons": list(s.reasons),
                "img": self.images.get(s.hero.id, ""),
            }
            for s in suggestions
        ]

    def _hero_items(self) -> list[dict]:
        items = []
        for hero in sorted(
            self.state.heroes.values(), key=lambda h: h.display_name.lower()
        ):
            probs = self.advisor.position_probs(hero.id)
            stats = self.state.hero_stats[hero.id]
            items.append(
                {
                    "id": hero.id,
                    "name": hero.display_name,
                    "attr": hero.primary_attr,
                    "attack": hero.attack_type,
                    "roles": list(hero.roles),
                    "cm_enabled": hero.cm_enabled,
                    "role": role_kind(probs),
                    "img": self.images.get(hero.id, ""),
                    "pro_ban": stats.pro_ban,
                    "pro_pick": stats.pro_pick,
                }
            )
        return items

    def _lineup(self, side: Side) -> dict | None:
        picks = self.state.side_picks(side)
        if len(picks) != 5:
            return None
        assignment = self.advisor.lineup_assignment(picks)
        if assignment is None:
            return None
        positions = [
            {
                "hero_id": hid,
                "name": self.state.hero_name(hid),
                "position": pos,
                "role": "core" if pos <= 3 else "support",
                "img": self.images.get(hid, ""),
            }
            for hid, pos in sorted(assignment.items(), key=lambda kv: kv[1])
        ]
        cores = sum(1 for p in positions if p["role"] == "core")
        return {"positions": positions, "cores": cores, "supports": 5 - cores}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            state = self.state
            turn = state.current_turn
            suggestions: list[Suggestion] = []
            if turn is not None:
                suggestions = self._suggestions(turn, limit=5)

            order = [
                {
                    "index": i,
                    "action": t.action,
                    "team": t.team,
                    "side": state.team_side(t.team),
                    "phase": t.phase,
                    "done": i < state.index,
                }
                for i, t in enumerate(state.order)
            ]

            def side_view(side: Side) -> dict:
                picks = [
                    {
                        "id": hid,
                        "name": state.hero_name(hid),
                        "img": self.images.get(hid, ""),
                    }
                    for hid in state.side_picks(side)
                ]
                bans = [
                    {
                        "id": hid,
                        "name": state.hero_name(hid),
                        "img": self.images.get(hid, ""),
                    }
                    for hid in state.side_bans(side)
                ]
                return {"picks": picks, "bans": bans}

            return {
                "step": state.index,
                "total": len(state.order),
                "done": state.done,
                "first_pick_side": state.first_pick_side,
                "turn": (
                    {
                        "action": turn.action,
                        "side": state.team_side(turn.team),
                        "phase": turn.phase,
                    }
                    if turn is not None
                    else None
                ),
                "radiant": side_view("radiant"),
                "dire": side_view("dire"),
                "order": order,
                "heroes": self._hero_items(),
                "suggestions": (
                    {
                        "action": turn.action,
                        "side": state.team_side(turn.team),
                        "items": self._suggestion_items(suggestions),
                    }
                    if turn is not None
                    else {"action": None, "side": None, "items": []}
                ),
                "lineups": {
                    "radiant": self._lineup("radiant"),
                    "dire": self._lineup("dire"),
                },
            }

    def apply(self, hero_id: int) -> None:
        with self.lock:
            self.state = self.state.apply_current(hero_id)
            self.advisor.bind(self.state)

    def apply_auto(self) -> None:
        with self.lock:
            turn = self.state.current_turn
            if turn is None:
                return
            side = self.state.team_side(turn.team)
            suggestion = (
                self.advisor.best_ban(side)
                if turn.action == "ban"
                else self.advisor.best_pick(side)
            )
            self.state = self.state.apply_current(
                suggestion.hero.id, expected_action=turn.action
            )
            self.advisor.bind(self.state)

    def undo(self) -> None:
        with self.lock:
            self.state = self.state.undo()
            self.advisor.bind(self.state)

    def reset(self) -> None:
        with self.lock:
            self.state = self.state.reset()
            self.advisor.bind(self.state)


class ApiHandler(BaseHTTPRequestHandler):
    session: DraftSession

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.info("%s %s", self.address_string(), fmt % args)

    # -- helpers ---------------------------------------------------------
    def _json(self, obj: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}")
            return {}
        return data if isinstance(data, dict) else {}

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._json(self.session.snapshot())
            return
        if path in ("/", "/index.html"):
            self._serve_file(WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            self._serve_file(WEB_DIR / path.removeprefix("/"))
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/action":
            data = self._body_json()
            raw_hero_id = data.get("hero_id")
            if raw_hero_id is None:
                self._error(HTTPStatus.BAD_REQUEST, "hero_id is required")
                return
            try:
                hero_id = int(raw_hero_id)
            except (TypeError, ValueError):
                self._error(HTTPStatus.BAD_REQUEST, "hero_id must be an integer")
                return
            try:
                self.session.apply(hero_id)
            except Exception as exc:  # InvalidMoveError etc.
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json(self.session.snapshot())
            return
        if path == "/api/auto":
            self.session.apply_auto()
            self._json(self.session.snapshot())
            return
        if path == "/api/undo":
            self.session.undo()
            self._json(self.session.snapshot())
            return
        if path == "/api/reset":
            self.session.reset()
            self._json(self.session.snapshot())
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _serve_file(self, path: Path) -> None:
        if not path.is_file() or WEB_DIR not in path.resolve().parents:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        mime, _ = mimetypes.guess_type(path.name)
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dota 2 draft tool web visualizer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--open", action="store_true", help="open the page in a browser after startup"
    )
    parser.add_argument("--side", choices=("radiant", "dire"), default="radiant")
    parser.add_argument("--order", choices=("7.40", "7.34"), default="7.40")
    parser.add_argument(
        "--lookahead", action="store_true", help="use beam-search lookahead"
    )
    parser.add_argument("--lookahead-depth", type=int, default=None)
    parser.add_argument(
        "--synergy",
        action="store_true",
        help="use synergy scoring (downloads if missing)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="refresh OpenDota data at startup"
    )
    parser.add_argument(
        "--refresh-synergy", action="store_true", help="rebuild pro-match synergy data"
    )
    parser.add_argument(
        "--log-level", default="WARNING", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    args = parser.parse_args(argv)
    setup_logging(level=getattr(logging, args.log_level))

    session = DraftSession(args)
    ApiHandler.session = session
    httpd = ThreadingHTTPServer((args.host, args.port), ApiHandler)

    url = f"http://{args.host}:{args.port}/"
    print(f"Draft visualizer running at {url}  (Ctrl+C to stop)")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
