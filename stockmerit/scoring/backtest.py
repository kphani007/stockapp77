"""Signal backtesting: forward-return and success-rate stats for a set of
historical signal dates, so a feature/score can be judged on whether it
actually predicts anything, rather than assumed to work.

This is "non-negotiable" per the StockMerit 2.0 roadmap: before a factor
earns weight in the MERIT SCORE, it should show a statistically meaningful
edge in an out-of-sample historical check, not just look plausible.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20, 30)


@dataclass
class SignalOutcome:
    signal_date: pd.Timestamp
    entry_price: float
    forward_returns: dict[int, float | None]
    max_drawdown_pct: float | None


def forward_returns(close: pd.Series, signal_dates: list,
                     horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> list[SignalOutcome]:
    """For each date a signal fired, compute the % return `close` made over
    each horizon (in trading sessions) after that date, plus the maximum
    drawdown from entry over the longest horizon. A horizon that runs past
    the end of available history is left as None rather than guessed."""
    c = close.dropna()
    idx = c.index
    max_h = max(horizons) if horizons else 0
    outcomes: list[SignalOutcome] = []
    for sig_date in signal_dates:
        if sig_date not in idx:
            earlier = idx[idx <= sig_date]
            if earlier.empty:
                continue
            sig_date = earlier[-1]
        pos = idx.get_loc(sig_date)
        entry = float(c.iloc[pos])
        rets: dict[int, float | None] = {}
        for h in horizons:
            rets[h] = float((c.iloc[pos + h] / entry - 1) * 100) if pos + h < len(c) else None
        window = c.iloc[pos:pos + max_h + 1]
        dd = None
        if len(window) > 1:
            running_max = window.cummax()
            dd = float(((window - running_max) / running_max * 100).min())
        outcomes.append(SignalOutcome(signal_date=sig_date, entry_price=entry,
                                       forward_returns=rets, max_drawdown_pct=dd))
    return outcomes


def summarize(outcomes: list[SignalOutcome],
              horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> pd.DataFrame:
    """Aggregate stats used to judge whether a signal deserves weight: average
    and median forward return, win rate, and a false-breakout rate (signals
    that ended up flat or negative) per horizon."""
    rows = []
    for h in horizons:
        vals = [o.forward_returns.get(h) for o in outcomes if o.forward_returns.get(h) is not None]
        if not vals:
            rows.append({"Horizon (sessions)": h, "Signals": 0, "Avg return %": None,
                         "Median return %": None, "Win rate %": None,
                         "False breakout rate %": None})
            continue
        arr = np.array(vals, dtype=float)
        rows.append({
            "Horizon (sessions)": h,
            "Signals": int(len(arr)),
            "Avg return %": round(float(arr.mean()), 2),
            "Median return %": round(float(np.median(arr)), 2),
            "Win rate %": round(float((arr > 0).mean() * 100), 1),
            "False breakout rate %": round(float((arr <= 0).mean() * 100), 1),
        })
    return pd.DataFrame(rows)


def avg_max_drawdown(outcomes: list[SignalOutcome]) -> float | None:
    dds = [o.max_drawdown_pct for o in outcomes if o.max_drawdown_pct is not None]
    return float(np.mean(dds)) if dds else None


# ------- ready-made signal detectors (technical, need OHLCV history only) -------

def rsi_oversold_dates(rsi_series: pd.Series, threshold: float = 30.0) -> list:
    """Dates where RSI crossed up through `threshold` from below -- an
    oversold-bounce entry signal."""
    r = rsi_series.dropna()
    crossed_up = (r.shift(1) < threshold) & (r >= threshold)
    return list(r.index[crossed_up])


def bollinger_breakout_dates(close: pd.Series, width: pd.Series, squeeze_pct: float = 20.0,
                              breakout_lookback: int = 10) -> list:
    """Dates where the band width was within its tightest `squeeze_pct`
    percentile of the trailing `breakout_lookback` sessions, and price then
    closed at a new high for that same lookback window -- a
    squeeze-then-breakout entry."""
    w = width.dropna()
    common = w.index.intersection(close.dropna().index)
    w, c = w.loc[common], close.loc[common]
    dates = []
    for i in range(breakout_lookback, len(c)):
        recent_w = w.iloc[i - breakout_lookback:i]
        was_squeezed = w.iloc[i - 1] <= recent_w.quantile(squeeze_pct / 100.0)
        broke_out = c.iloc[i] >= c.iloc[i - breakout_lookback:i].max()
        if was_squeezed and broke_out:
            dates.append(c.index[i])
    return dates


SIGNAL_LIBRARY = {
    "RSI oversold bounce (crosses above 30)": "rsi_oversold",
    "Bollinger squeeze breakout": "bollinger_breakout",
}
