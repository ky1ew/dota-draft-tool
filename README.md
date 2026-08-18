# Dota 2 Draft Tool — Prototype Engine

A small, working Captain's Mode ban/pick assistant for Dota 2 teams.

This prototype implements the core engine from the design proposal:

1. **Data-driven draft order** — current post-7.40 Captain's Mode sequence
   (14 bans, 10 picks) plus the 7.34 sequence for comparison.
2. **Live OpenDota data** — hero metadata, patch stats (`cm_enabled`,
   pub/pro win rates), the pairwise hero-vs-hero matchup matrix, and
   empirical position data inferred from parsed matches.
3. **Explainable suggestion engine** — top-N bans and picks with reasons,
   using Bayesian-shrunk matchup win rates, position needs, role coverage,
   and meta priority.
4. **Enforced lineup shape** — every simulated lineup ends as 3 cores
   (positions 1-3) and 2 supports (positions 4-5). Dual-role heroes such
   as Io and Windranger are modeled as flex.
5. **CLI + full-draft simulator** — interactive drafting or a greedy
   simulated draft.

## Quick start

```bash
# first run downloads OpenDota data (~2 minutes, cached afterwards)
python3 -m draft_engine.cli --demo

# interactive mode
python3 -m draft_engine.cli
```

Refresh all cached data:

```bash
python3 -m draft_engine.cli --demo --refresh
```

Start with Dire having first pick or use the pre-7.40 order:

```bash
python3 -m draft_engine.cli --side dire --order 7.34
```

## Project layout

```
draft_engine/
  data.py      OpenDota fetch + disk cache
  models.py    Hero/Stats dataclasses, DraftState, configurable turn order
  scoring.py   DraftAdvisor: matchup + role + meta scoring with reasons
  cli.py       interactive CLI and --demo simulator
cache/         downloaded JSON (heroes, hero_stats, matchups/*)
```

## Draft order (patch 7.40+)

`F` = team with first pick, `S` = team with second pick.

| Phase | Sequence |
|---|---|
| Ban 1 | F F S S F S S |
| Pick 1 | F S |
| Ban 2 | F F S |
| Pick 2 | S F F S S F |
| Ban 3 | F S F S |
| Pick 3 | F S |

Totals: 7 bans and 5 picks per team. The engine maps `F/S` to
Radiant/Dire with `--side`.

## Position model

OpenDota's `heroStats` `1_pick`..`8_pick` fields are **rank brackets**, not
positions, so they are deliberately not used for roles.

Instead, position probabilities are inferred from recent parsed matches:

* lane_role 2 -> position 2 (mid)
* lane_role 4 / roaming -> position 4
* safe-lane pair: higher GPM -> position 1, lower GPM -> position 5
* off-lane pair: higher GPM -> position 3, lower GPM -> position 4

Each hero is classified **core / support / flex**. A candidate is rejected
if adding it makes a final 3-core + 2-support lineup impossible. Heroes
with sparse recent data use a curated fallback map.

## Scoring summary

**Pick score** =
matchup edge vs revealed enemies
+ core/support position-coverage contribution
+ missing role-tag coverage (initiation/disable/waveclear/durable...)
+ meta strength (pub winrate, pro pick/ban priority)
+ early-pick flexibility bonus.

**Ban score** =
how hard the hero counters our revealed picks
+ position/role denial vs the enemy lineup
+ meta ban priority.

Matchup win rates use Bayesian shrinkage toward 50%:

```
wr = (wins + 20 * 0.5) / (games + 20)
```

so a 6-1 record doesn't dominate the suggestion list. If a direct matchup
row is missing, the engine uses the reverse matchup as an approximation.

## Known prototype limitations

- Synergy (winrate *with* a hero) is still approximated with role fit; a real
  product should compute true same-team pair win rates from match history.
- No opponent/player hero-pool data yet.
- No win-probability model or lookahead yet (greedy suggestions only).
- Public-match matchup data is a directional signal, not pro-level truth.
