"""Exception hierarchy for the draft engine."""

from __future__ import annotations


class DraftEngineError(Exception):
    """Base class for all draft-engine errors."""


class DataFetchError(DraftEngineError, RuntimeError):
    """A data source could not be fetched or parsed."""

    def __init__(self, resource: str, reason: str):
        self.resource = resource
        self.reason = reason
        super().__init__(f"failed to fetch {resource}: {reason}")


class InvalidMoveError(DraftEngineError, ValueError):
    """The requested pick/ban is illegal for the current draft state."""


class HeroNotFoundError(DraftEngineError, LookupError):
    """A hero query could not be resolved."""

    def __init__(self, query: str):
        self.query = query
        super().__init__(f"hero not found: {query}")


class SuggestionError(DraftEngineError, RuntimeError):
    """The suggestion engine could not produce a legal recommendation."""
