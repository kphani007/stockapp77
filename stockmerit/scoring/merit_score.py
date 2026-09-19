"""Composite MERIT SCORE: a weighted blend of the Phase-1 signal features.

Every component score is 0-100, with 50 as neutral/unknown-but-average. The
DEFAULT_WEIGHTS below are a reasonable starting point, NOT empirically
validated -- use stockmerit.scoring.backtest to check which components
actually carry predictive value before trusting this on real decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class MeritComponents:
    relative_strength: float | None = None
    sector_strength: float | None = None
    market_regime: float | None = None
    earnings_acceleration: float | None = None
    cash_flow_quality: float | None = None
    peg: float | None = None
    bollinger_squeeze: float | None = None
    volume_trend: float | None = None
    accumulation: float | None = None


DEFAULT_WEIGHTS: dict[str, float] = {
    "relative_strength": 0.20,
    "sector_strength": 0.10,
    "market_regime": 0.05,
    "earnings_acceleration": 0.15,
    "cash_flow_quality": 0.15,
    "peg": 0.10,
    "bollinger_squeeze": 0.10,
    "volume_trend": 0.05,
    "accumulation": 0.10,
}
assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9, "DEFAULT_WEIGHTS must sum to 1.0"


def compute_merit_score(components: MeritComponents,
                         weights: dict[str, float] | None = None) -> tuple[float | None, dict]:
    """Weighted average of whichever components are available, re-normalizing
    weights over just the present ones -- a stock with missing data for one
    factor isn't penalized with an implicit zero for it.

    Returns (score, breakdown); breakdown maps each present component name to
    (raw_score, normalized_weight_used). score is None if no component
    could be computed at all.
    """
    weights = weights or DEFAULT_WEIGHTS
    present = {f.name: getattr(components, f.name) for f in fields(components)
               if getattr(components, f.name) is not None}
    if not present:
        return None, {}
    total_w = sum(weights.get(name, 0.0) for name in present)
    if not total_w:
        return None, {}
    breakdown = {name: (val, weights.get(name, 0.0) / total_w) for name, val in present.items()}
    score = sum(val * w for val, w in breakdown.values())
    return float(round(score, 1)), breakdown
