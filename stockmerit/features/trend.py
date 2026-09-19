"""Broad-market regime and sector-strength features."""

from __future__ import annotations

import pandas as pd


def market_regime(index_close: pd.Series, fast: int = 50, slow: int = 200) -> tuple[str, float]:
    """Classify the broad market from a benchmark index's price vs its
    50/200-day SMAs. Returns (label, score 0-100): a confirmed uptrend
    (price above both SMAs, fast above slow) scores high, a confirmed
    downtrend scores low, anything mixed is "Neutral" at 50."""
    c = index_close.dropna()
    if len(c) < slow:
        return "Unknown", 50.0
    price = float(c.iloc[-1])
    sma_fast = float(c.rolling(fast).mean().iloc[-1])
    sma_slow = float(c.rolling(slow).mean().iloc[-1])
    above_fast, above_slow, fast_above_slow = price > sma_fast, price > sma_slow, sma_fast > sma_slow
    if above_fast and above_slow and fast_above_slow:
        return "Bullish", 80.0
    if not above_fast and not above_slow and not fast_above_slow:
        return "Bearish", 20.0
    return "Neutral", 50.0


def sector_strength_score(sector_avg_return_pct: float | None,
                           benchmark_return_pct: float | None) -> float | None:
    """How a sector's average return over some window compares to the
    benchmark's return over the same window, mapped to a 0-100 score
    centered on 50 (the sector performing in line with the market)."""
    if sector_avg_return_pct is None or benchmark_return_pct is None:
        return None
    diff = sector_avg_return_pct - benchmark_return_pct
    scaled = 50.0 + (diff / 15.0) * 50.0  # +/-15pp relative outperformance saturates 0/100
    return float(max(0.0, min(100.0, scaled)))
