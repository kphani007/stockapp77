"""Fundamental-signal features derived from quarterly financial statements.

Each function takes the same yfinance `Ticker.quarterly_income_stmt` /
`quarterly_cashflow` style DataFrame (rows are line items, columns are
report dates) already used elsewhere in this app for the health scorecard.
"""

from __future__ import annotations

import pandas as pd


def _quarterly_series(stmt: pd.DataFrame | None, *row_keys: str) -> pd.Series | None:
    if stmt is None or getattr(stmt, "empty", True):
        return None
    for key in row_keys:
        for idx in stmt.index:
            if key.lower() in str(idx).lower():
                s = pd.Series(stmt.loc[idx]).dropna().astype(float)
                if not s.empty:
                    return s.sort_index(ascending=False)
    return None


def earnings_acceleration(quarterly_income_stmt: pd.DataFrame | None,
                           quarters: int = 4) -> float | None:
    """Is quarter-over-quarter earnings growth itself speeding up? Returns
    the change (in percentage points) between the most recent QoQ growth
    rate and the one before it, using up to `quarters` most-recent quarters
    of Net Income. Positive = accelerating, negative = decelerating."""
    ni = _quarterly_series(quarterly_income_stmt, "Net Income")
    if ni is None or len(ni) < quarters:
        return None
    recent = ni.head(quarters).iloc[::-1]  # oldest -> newest
    growth = recent.pct_change().dropna() * 100
    if len(growth) < 2:
        return None
    return float(growth.iloc[-1] - growth.iloc[-2])


def earnings_acceleration_score(accel_pp: float | None) -> float | None:
    if accel_pp is None:
        return None
    scaled = 50.0 + (accel_pp / 20.0) * 50.0  # +/-20pp change in growth rate saturates 0/100
    return float(max(0.0, min(100.0, scaled)))


def cash_flow_quality(quarterly_cashflow: pd.DataFrame | None,
                       quarterly_income_stmt: pd.DataFrame | None,
                       quarters: int = 4) -> float | None:
    """Ratio of operating cash flow to net income summed over the trailing
    `quarters` quarters. >=1x means reported profit is backed by (or
    exceeded by) real cash generation; well below that is a quality flag."""
    cfo = _quarterly_series(quarterly_cashflow, "Operating Cash Flow",
                             "Total Cash From Operating")
    ni = _quarterly_series(quarterly_income_stmt, "Net Income")
    if cfo is None or ni is None:
        return None
    ni_sum = float(ni.head(quarters).sum())
    if not ni_sum:
        return None
    return float(cfo.head(quarters).sum()) / ni_sum


def cash_flow_quality_score(ratio: float | None) -> float | None:
    if ratio is None:
        return None
    scaled = (ratio - 0.5) / 0.5 * 100.0  # 0.5x -> 0, 1.0x -> 100, saturates above
    return float(max(0.0, min(100.0, scaled)))


def peg_score(peg: float | None) -> float | None:
    """Lower PEG is better (cheap relative to growth): ~1.0 is fair value,
    well below is a bargain signal, well above is rich."""
    if peg is None or peg <= 0:
        return None
    scaled = 100.0 - (peg - 0.5) / 2.0 * 100.0
    return float(max(0.0, min(100.0, scaled)))
