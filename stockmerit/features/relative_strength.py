"""Relative strength: how a stock's return compares to a benchmark's."""

from __future__ import annotations

import pandas as pd


def relative_strength_ratio(stock_close: pd.Series, benchmark_close: pd.Series,
                             lookback: int = 63) -> float | None:
    """(1 + stock return) / (1 + benchmark return) over `lookback` sessions.
    >1 means the stock outperformed the benchmark over that window."""
    s, b = stock_close.dropna(), benchmark_close.dropna()
    if len(s) <= lookback or len(b) <= lookback:
        return None
    s_ret = float(s.iloc[-1] / s.iloc[-1 - lookback] - 1)
    b_ret = float(b.iloc[-1] / b.iloc[-1 - lookback] - 1)
    denom = 1 + b_ret
    return (1 + s_ret) / denom if denom else None


def relative_strength_score(stock_close: pd.Series, benchmark_close: pd.Series,
                             lookback: int = 63) -> float | None:
    """Map the RS ratio to a 0-100 score centered on 50 (ratio == 1.0).

    This is a fixed, symmetric heuristic mapping, not an IBD-style RS Rating
    (which needs a full peer universe to percentile-rank against) -- treat it
    as a rough signal and validate with the backtest engine before relying
    on it."""
    ratio = relative_strength_ratio(stock_close, benchmark_close, lookback)
    if ratio is None:
        return None
    pct_diff = (ratio - 1.0) * 100  # +/-30% relative outperformance saturates 0/100
    scaled = 50.0 + (pct_diff / 30.0) * 50.0
    return float(max(0.0, min(100.0, scaled)))
