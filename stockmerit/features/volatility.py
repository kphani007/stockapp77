"""Volatility-based signal features: Bollinger band width and squeeze."""

from __future__ import annotations

import pandas as pd


def bollinger_width(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Bollinger band width normalized by the middle band (SMA).

    A falling width means the trading range is contracting (a "squeeze"),
    which often precedes a breakout; a rising width means it is expanding.
    """
    c = close.dropna()
    mid = c.rolling(window).mean()
    std = c.rolling(window).std(ddof=0)
    upper, lower = mid + num_std * std, mid - num_std * std
    return ((upper - lower) / mid).rename("bb_width")


def bollinger_squeeze_percentile(close: pd.Series, window: int = 20, num_std: float = 2.0,
                                  lookback: int = 126) -> float | None:
    """Where today's band width sits within its own last `lookback` sessions:
    0 = the tightest squeeze in that window, 100 = the widest. A low value
    flags a squeeze that is statistically more likely to resolve soon."""
    width = bollinger_width(close, window, num_std).dropna()
    if len(width) < window + 5:
        return None
    recent = width.tail(lookback)
    today = recent.iloc[-1]
    return float((recent <= today).mean() * 100)


def bollinger_score(close: pd.Series, window: int = 20, num_std: float = 2.0,
                     lookback: int = 126) -> float | None:
    """Inverted squeeze percentile so a tighter squeeze scores higher (0-100),
    matching the "volatility contracting" signal called out as a setup."""
    pct = bollinger_squeeze_percentile(close, window, num_std, lookback)
    return None if pct is None else float(100.0 - pct)
