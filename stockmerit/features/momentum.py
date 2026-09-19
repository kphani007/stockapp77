"""Momentum features computed as full time series (needed for backtesting
signal dates), rather than just the latest value the screener shows."""

from __future__ import annotations

import pandas as pd


def rolling_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI as a full series -- same formula as the app's single-value
    RSI, but keeping every day so a backtest can find every historical
    crossing rather than only today's reading."""
    c = close.dropna()
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.where(avg_loss != 0.0, 100.0).rename("rsi")
