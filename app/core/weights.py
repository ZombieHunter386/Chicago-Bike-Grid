"""Routing weight tables — single source for spec (2026-07-29 LTS-4) §4.

Tier names map to user-facing labels in the UI:
    "kid"           → "Safe for kid"  (LTS 1 only)
    "inexperienced" → "Inexperienced" (LTS 1-2)
    "experienced"   → "Experienced"   (LTS 1-3)
    "death_wish"    → "Death wish"    (LTS 1-4)

Main weights enforce hard tier cutoffs (∞ for disallowed LTS levels);
fallback weights are applied when the main-weight route returns no path.
Both tables read from this file so values cannot drift between code and
prep/config/routing_weights.yaml (the canonical spec copy).

INF_WEIGHT detection: routing.py checks whether ANY edge in a Dijkstra
result has weight ≥ INF_WEIGHT (rather than thresholding total cost),
which is robust to long-but-legitimate paths whose summed weight could
otherwise approach a chosen threshold.
"""
from __future__ import annotations

# Hard-cutoff sentinel. Any edge weighted at INF_WEIGHT effectively bars
# routing through it. Routing detects "no in-tier path" by checking
# `any(weights[e] >= INF_WEIGHT for e in epath)` — never via summed-cost
# threshold (which can misfire on long routes).
INF_WEIGHT = 1e9

# Index i = LTS (i+1). Four entries per table: LTS 1..4.
TIERS: dict[str, dict[str, list[float]]] = {
    "kid": {
        "main":     [1.0, INF_WEIGHT, INF_WEIGHT, INF_WEIGHT],
        "fallback": [1.0, 5.0, 20.0, 40.0],
    },
    "inexperienced": {
        "main":     [1.0, 1.2, INF_WEIGHT, INF_WEIGHT],
        "fallback": [1.0, 1.2, 10.0, 20.0],
    },
    "experienced": {
        "main":     [1.0, 1.2, 1.5, INF_WEIGHT],
        "fallback": [1.0, 1.2, 1.5, 10.0],
    },
    "death_wish": {
        "main":     [1.0, 1.2, 1.5, 2.0],
        "fallback": [1.0, 1.2, 1.5, 2.0],
    },
}


def _validate_lts(lts: int) -> None:
    if lts not in (1, 2, 3, 4):
        raise ValueError(f"lts must be 1..4 (got {lts})")


def main_weight_for(tier: str, lts: int) -> float:
    _validate_lts(lts)
    return TIERS[tier]["main"][lts - 1]


def fallback_weight_for(tier: str, lts: int) -> float:
    _validate_lts(lts)
    return TIERS[tier]["fallback"][lts - 1]
