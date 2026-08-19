from __future__ import annotations

import logging

import pytest

from draft_engine.cli import print_suggestions, resolve_hero
from draft_engine.exceptions import (
    DataFetchError,
    DraftEngineError,
    HeroNotFoundError,
    InvalidMoveError,
    SuggestionError,
)
from draft_engine.logging_config import setup_logging
from draft_engine.scoring import Suggestion


def test_print_suggestions(capsys):
    hero = next(iter({}.values())) if False else None
    from draft_engine.models import Hero

    hero = Hero(
        id=1,
        name="npc_a",
        localized_name="Anti-Mage",
        primary_attr="agi",
        attack_type="Melee",
        roles=("Carry",),
    )
    print_suggestions("Top picks", [Suggestion(hero, 10.0, ("reason",))])
    out = capsys.readouterr().out
    assert "Anti-Mage" in out
    assert "reason" in out


def test_resolve_hero(synthetic_state):
    assert resolve_hero(synthetic_state, "1") == 1
    assert resolve_hero(synthetic_state, "Hero 3") == 3
    with pytest.raises(HeroNotFoundError):
        resolve_hero(synthetic_state, "DoesNotExist")


def test_exception_hierarchy():
    assert isinstance(DataFetchError("x", "y"), DraftEngineError)
    assert isinstance(InvalidMoveError("no"), DraftEngineError)
    assert isinstance(HeroNotFoundError("q"), DraftEngineError)
    assert isinstance(SuggestionError("no"), DraftEngineError)


def test_logging_setup():
    # Should configure the root logger without raising.
    setup_logging(level=logging.DEBUG)
    assert logging.getLogger().level == logging.DEBUG
