"""Technical indicators implemented in plain pandas/numpy (no third-party TA library).

All functions take a DataFrame with lowercase columns: open, high, low, close, volume.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockmerit.features.momentum import rolling_rsi


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI. Delegates to StockMerit's own implementation so the screener
    and the agent can never disagree on an RSI value."""
    return rolling_rsi(close, n)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * _wilder(plus_dm, n) / a
    mdi = 100 * _wilder(minus_dm, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({"adx": _wilder(dx, n), "plus_di": pdi, "minus_di": mdi})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Returns columns: supertrend (line), direction (+1 up / -1 down)."""
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    st = np.full(len(df), np.nan)
    direction = np.zeros(len(df))
    for i in range(len(df)):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(st[i - 1]):
            direction[i] = 1 if close[i] >= hl2[i] else -1
            st[i] = lower[i] if direction[i] == 1 else upper[i]
            continue
        # carry bands forward so they only tighten
        if not (lower[i] > lower[i - 1] or close[i - 1] < lower[i - 1]):
            lower[i] = lower[i - 1]
        if not (upper[i] < upper[i - 1] or close[i - 1] > upper[i - 1]):
            upper[i] = upper[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < lower[i] else 1
        else:
            direction[i] = 1 if close[i] > upper[i] else -1
        st[i] = lower[i] if direction[i] == 1 else upper[i]
    return pd.DataFrame({"supertrend": st, "direction": direction}, index=df.index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset each trading session (intraday bars with a DatetimeIndex)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return pv / vol.replace(0, np.nan)


def pivot_levels(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Classic floor pivots plus CPR from the previous session's H/L/C."""
    p = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = 2 * p - bc
    return {
        "pivot": p,
        "r1": 2 * p - prev_low,
        "s1": 2 * p - prev_high,
        "r2": p + (prev_high - prev_low),
        "s2": p - (prev_high - prev_low),
        "r3": prev_high + 2 * (p - prev_low),
        "s3": prev_low - 2 * (prev_high - p),
        "cpr_top": max(tc, bc),
        "cpr_bottom": min(tc, bc),
        "cpr_width_pct": abs(tc - bc) / p * 100,
    }


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Latest bar volume divided by the average of the previous n bars."""
    return volume / volume.shift(1).rolling(n).mean()


def relative_strength(close: pd.Series, bench: pd.Series, n: int = 60) -> float | None:
    """Stock return minus benchmark return over n bars, in percentage points."""
    j = pd.concat([close, bench], axis=1, join="inner").dropna()
    if len(j) <= n:
        return None
    s = j.iloc[-1, 0] / j.iloc[-1 - n, 0] - 1
    b = j.iloc[-1, 1] / j.iloc[-1 - n, 1] - 1
    return float((s - b) * 100)
