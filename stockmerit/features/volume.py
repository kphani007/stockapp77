"""Volume-based signal features: contraction/expansion and accumulation."""

from __future__ import annotations

import numpy as np
import pandas as pd


def volume_trend(vol: pd.Series, short: int = 5, long: int = 20) -> float | None:
    """Ratio of short-window average volume to long-window average volume.
    >1 means volume is expanding recently relative to its own base rate,
    <1 means it is contracting."""
    v = vol.dropna()
    if len(v) < long:
        return None
    long_avg = float(v.tail(long).mean())
    if not long_avg:
        return None
    return float(v.tail(short).mean()) / long_avg


def volume_trend_score(ratio: float | None) -> float | None:
    """Map the expansion/contraction ratio to 0-100, centered at 50 (ratio
    == 1.0, i.e. volume in line with its own recent base rate)."""
    if ratio is None or ratio <= 0:
        return None
    scaled = 50.0 + (ratio - 1.0) * 50.0
    return float(max(0.0, min(100.0, scaled)))


def accumulation_distribution(high: pd.Series, low: pd.Series, close: pd.Series,
                               vol: pd.Series) -> pd.Series:
    """Classic Chaikin Accumulation/Distribution line."""
    df = pd.concat([high, low, close, vol], axis=1)
    df.columns = ["high", "low", "close", "vol"]
    df = df.dropna()
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    mfm = (((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng).fillna(0.0)
    return (mfm * df["vol"]).cumsum().rename("ad_line")


def accumulation_score(high: pd.Series, low: pd.Series, close: pd.Series, vol: pd.Series,
                        lookback: int = 20) -> float | None:
    """Trend of the Accumulation/Distribution line over `lookback` sessions,
    normalized against the line's own dispersion so it's comparable across
    stocks with very different volume. This is a heuristic z-style score,
    not a calibrated probability -- validate with the backtest engine."""
    ad = accumulation_distribution(high, low, close, vol)
    if len(ad) < lookback + 1:
        return None
    window = ad.tail(lookback).astype(float)
    x = np.arange(len(window), dtype=float)
    slope = float(np.polyfit(x, window.to_numpy(), 1)[0])
    spread = float(window.std(ddof=0)) or 1.0
    z = max(min((slope * len(window)) / spread, 2.0), -2.0)
    return float(50.0 + 25.0 * (z / 2.0))
