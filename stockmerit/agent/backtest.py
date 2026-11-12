"""Simple daily-bar, long-only backtests for strategy validation (spec section 12).

Deliberately conservative: signal on the close, entry at the next open plus slippage,
stop checked before target inside a bar, costs on both sides, one position at a time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

STRATEGIES = {
    "ema_pullback": "Uptrend (EMA20>EMA50>EMA200); bar dips to EMA20 and closes back above it.",
    "breakout_20d": "Close above the prior 20-day high on volume >= 1.5x the 20-day average, above EMA200.",
}


def _signals(df: pd.DataFrame, strategy: str) -> pd.Series:
    c = df["close"]
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    if strategy == "ema_pullback":
        return (e20 > e50) & (e50 > e200) & (df["low"] <= e20) & (c > e20)
    if strategy == "breakout_20d":
        prior_high = df["high"].shift(1).rolling(20).max()
        return (c > prior_high) & (ind.relative_volume(df["volume"]) >= 1.5) & (c > e200)
    raise ValueError(f"unknown strategy '{strategy}'. Valid: {list(STRATEGIES)}")


def _stats(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"trades": 0}
    r = tr["ret_pct"]
    wins, losses = r[r > 0], r[r <= 0]
    eq = (1 + r / 100).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    gp, gl = wins.sum(), -losses.sum()
    return {
        "trades": int(len(r)),
        "win_rate_pct": round(len(wins) / len(r) * 100, 1),
        "avg_win_pct": round(wins.mean(), 2) if len(wins) else None,
        "avg_loss_pct": round(losses.mean(), 2) if len(losses) else None,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "expectancy_pct_per_trade": round(r.mean(), 2),
        "avg_r_multiple": round(tr["r_multiple"].mean(), 2),
        "total_return_pct_compounded": round((eq.iloc[-1] - 1) * 100, 1),
        "max_drawdown_pct": round(dd, 1),
    }


def run(df: pd.DataFrame, strategy: str = "ema_pullback", atr_stop_mult: float = 1.5,
        reward_r: float = 2.0, max_hold_bars: int = 20, cost_pct_per_side: float = 0.12,
        slippage_pct_per_side: float = 0.05, oos_fraction: float = 0.3) -> dict:
    if len(df) < 260:
        return {"error": f"need at least 260 daily bars, got {len(df)}"}
    sig = _signals(df, strategy).fillna(False).to_numpy()
    a = ind.atr(df).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    friction = (cost_pct_per_side + slippage_pct_per_side) / 100
    trades, i, n = [], 200, len(df)
    while i < n - 1:
        if not sig[i] or np.isnan(a[i]):
            i += 1
            continue
        entry = o[i + 1]
        risk = atr_stop_mult * a[i]
        stop, target = entry - risk, entry + reward_r * risk
        exit_px, exit_i, reason = None, None, None
        for j in range(i + 1, min(i + 1 + max_hold_bars, n)):
            if l[j] <= stop:                       # stop first: worst case inside the bar
                exit_px, exit_i, reason = min(o[j], stop), j, "stop"
                break
            if h[j] >= target:
                exit_px, exit_i, reason = target, j, "target"
                break
        if exit_px is None:
            exit_i = min(i + max_hold_bars, n - 1)
            exit_px, reason = c[exit_i], "time"
        buy, sell = entry * (1 + friction), exit_px * (1 - friction)
        trades.append({"entry_date": df.index[i + 1], "exit_date": df.index[exit_i],
                       "ret_pct": (sell / buy - 1) * 100,
                       "r_multiple": (sell - buy) / risk, "exit": reason})
        i = exit_i + 1
    tr = pd.DataFrame(trades)
    out = {
        "strategy": strategy, "rules": STRATEGIES[strategy],
        "period": f"{df.index[0].date()} to {df.index[-1].date()}", "bars": n,
        "assumptions": {"entry": "next open after signal", "stop": f"{atr_stop_mult} x ATR14",
                        "target": f"{reward_r}R", "max_hold_bars": max_hold_bars,
                        "cost_pct_per_side": cost_pct_per_side,
                        "slippage_pct_per_side": slippage_pct_per_side,
                        "position": "one at a time, fully invested per trade, long only"},
        "overall": _stats(tr),
    }
    if not tr.empty:
        cut = df.index[int(n * (1 - oos_fraction))]
        out["earlier_period"] = _stats(tr[tr["entry_date"] < cut])
        out["later_period"] = _stats(tr[tr["entry_date"] >= cut])
        out["split_date"] = str(cut.date())
        out["by_year"] = {str(y): _stats(g) for y, g in tr.groupby(tr["entry_date"].dt.year)}
        out["exit_reasons"] = tr["exit"].value_counts().to_dict()
    nt = out["overall"].get("trades", 0)
    out["caveats"] = [
        "Past backtest performance does not guarantee future results.",
        "Single symbol tested: results suffer from survivorship/selection bias and small samples.",
        "Parameters are fixed, not optimised, so the earlier/later split is a stability check, "
        "not a true out-of-sample test of a fitted model.",
        "Daily bars cannot tell whether stop or target was hit first inside a bar; stop is assumed first.",
    ]
    if nt < 30:
        out["caveats"].insert(0, f"Only {nt} trades: far too few to conclude the strategy works.")
    return out
