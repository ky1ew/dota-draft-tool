"""Central configuration.

All tunable weights and thresholds live here so they can be adjusted
without hunting through the scoring code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringWeights:
    """Weights for the explainable pick/ban scorer."""

    # Matchup edge vs revealed enemy heroes (percentage points -> score).
    matchup_edge: float = 90.0

    # Position-assignment contribution for a pick.
    position_gain: float = 35.0
    lineup_complete_bonus: float = 4.0
    first_pick_position_weight: float = 10.0

    # Early-pick flexibility (entropy of position distribution).
    early_flex_entropy: float = 8.0
    flex_bonus: float = 1.0

    # Role-tag coverage (initiation, disable, waveclear, ...).
    role_tag_gain: float = 12.0

    # Meta-score weights.
    meta_pub_winrate: float = 60.0
    meta_pro_ban: float = 30.0
    meta_pro_pick: float = 15.0
    meta_pro_winrate: float = 20.0

    # Same-team pair synergy.
    team_synergy: float = 60.0
    ban_enemy_synergy: float = 40.0

    # Ban-specific weights.
    ban_counter_threat: float = 120.0
    ban_position_denial_multiplier: float = 0.9
    ban_early_entropy: float = 4.0
    ban_meta_multiplier: float = 0.9
    ban_role_tag_gain: float = 8.0


@dataclass(frozen=True)
class MatchupConfig:
    pseudo_count: float = 20.0
    min_games_for_reason: int = 10


@dataclass(frozen=True)
class RoleConfig:
    dirichlet_prior: float = 0.35
    min_position_prob: float = 0.05
    min_role_prob: float = 0.08
    min_empirical_games: int = 10
    fallback_base_prob: float = 0.02
    fallback_weight: float = 0.9
    assignment_cache_size: int = 1024


@dataclass(frozen=True)
class DataConfig:
    base_url: str = "https://api.opendota.com/api"
    request_delay: float = 1.1
    timeout: int = 30
    explorer_timeout: int = 90
    position_min_match_id: int = 8_900_000_000


@dataclass(frozen=True)
class SearchConfig:
    beam_width: int = 6
    max_depth: int = 2


@dataclass(frozen=True)
class EngineConfig:
    scoring: ScoringWeights = ScoringWeights()
    matchup: MatchupConfig = MatchupConfig()
    roles: RoleConfig = RoleConfig()
    data: DataConfig = DataConfig()
    search: SearchConfig = SearchConfig()


DEFAULT_CONFIG = EngineConfig()
