# Dota 2 Draft Tool

A working Captain's Mode ban/pick assistant for Dota 2 teams.

The engine simulates the current draft order, scores every legal pick/ban
with explainable reasons, and **enforces a valid 3-core + 2-support lineup
shape** (positions 1-3 core, positions 4-5 support).

## Features

- Current post-7.40 Captain's Mode order (14 bans + 10 picks), plus 7.34.
- **Immutable `DraftState`**: every move returns a new state, so undo,
  caching and future lookahead search are safe.
- **Empirical position model**: core/support/flex classification inferred
  from parsed matches (lane + GPM), with a curated fallback for sparse data.
- **Explainable suggestions**: Bayesian-shrunk matchup win rates, position
  fit, role coverage, meta priority and (optional) same-team pair synergy.
- **Consolidated cache**: one `matchups.json` + O(1) matchup lookup and LRU
  assignment caching.
- **Synergy model** (optional): winrate-when-together learned from recent
  professional matches via OpenDota parsed matches.
- CLI demo, interactive mode, and a pytest suite.

## Quick start

```bash
# First run downloads OpenDota data (~2 minutes, cached afterwards)
python3 -m draft_engine --demo

# Interactive drafting
python3 -m draft_engine

# Include same-team pair synergy (downloads pro matches if missing)
python3 -m draft_engine --demo --synergy
```

Refresh cached data:

```bash
python3 -m draft_engine --demo --refresh
python3 -m draft_engine --demo --refresh-synergy
```

Start with Dire first pick or use the pre-7.40 order:

```bash
python3 -m draft_engine --side dire --order 7.34
```

Run tests:

```bash
python3 -m pytest tests/
```

## Project layout

```
draft_engine/
  config.py        All scoring/data/role weights (no magic numbers)
  data.py          OpenDota fetch + consolidated cache + migration
  exceptions.py    DraftEngineError hierarchy
  logging_config.py
  models.py        Frozen Hero/Stats/Turn + immutable DraftState
  roles.py         Position 1-5 model, 3-core/2-support feasibility, LRU cache
  scoring.py       Explainable pick/ban scoring engine
  synergy.py       Same-team pair winrate model from pro matches
  cli.py           Interactive CLI, --demo simulator, --synergy, --log-level
tests/
  unit/integration pytest suite
cache/             Downloaded data (git-ignored)
```

## Position model

OpenDota's `heroStats` `1_pick`..`8_pick` fields are **rank brackets**, not
positions, so they are deliberately not used for roles.

Positions are inferred from recent parsed matches:

| Observation | Position |
|---|---|
| mid lane | 2 |
| jungle / roaming | 4 |
| safe lane, higher GPM | 1 |
| safe lane, lower GPM | 5 |
| off lane, higher GPM | 3 |
| off lane, lower GPM | 4 |

Heroes are classified `core` / `support` / `flex` (e.g. Io and Windranger
are flex). A pick candidate is rejected if it would make a final
3-core + 2-support lineup impossible.

## Scoring summary

**Pick score** =

- matchup edge vs revealed enemies
- same-team pair synergy (when enabled)
- core/support position-coverage contribution
- missing role-tag coverage (initiation, disable, waveclear, durable, ...)
- meta strength (pub winrate, pro pick/ban priority)
- early-pick flexibility bonus

**Ban score** =

- how hard the hero counters our revealed picks
- enemy pair-synergy denial (when enabled)
- position/role denial vs the enemy lineup
- meta ban priority

Matchup and synergy win rates use Bayesian shrinkage toward 50%:

```
wr = (wins + 20 * 0.5) / (games + 20)
```

so a 6-1 record doesn't dominate the suggestion list. If a direct matchup
row is missing, the engine uses the reverse matchup as an approximation.

## Configuration

All tunable weights live in `draft_engine/config.py`:

```python
@dataclass(frozen=True)
class ScoringWeights:
    matchup_edge: float = 90.0
    position_gain: float = 35.0
    team_synergy: float = 60.0
    ...
```

Adjusting a weight does not require touching scoring code.

## Known limitations / next steps

- Lookahead search (beam/minimax) is the natural next feature now that
  `DraftState` is immutable.
- No opponent/player hero-pool data yet.
- Public/pro-match samples are directional, not absolute truth.
- Synergy samples are small; reasons are only shown at `n >= 10`.

## License

No license selected yet.
