"""Technical summaries, fundamentals and the 100-point setup score.

Rule: a value that cannot be computed is returned as None and named in `unavailable`.
Nothing here guesses or back-fills.
"""
from __future__ import annotations

import math

import pandas as pd

from . import data, indicators as ind


def _r(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _last(s: pd.Series):
    return _r(s.iloc[-1]) if len(s) else None


# ------------------------------------------------------------------ technicals
def daily_summary(df: pd.DataFrame, bench: pd.DataFrame | None = None) -> dict:
    c, v = df["close"], df["volume"]
    m = ind.macd(c)
    a = ind.adx(df)
    out = {
        "bars": len(df),
        "close": _last(c),
        "prev_close": _r(c.iloc[-2]) if len(c) > 1 else None,
        "change_pct": _r((c.iloc[-1] / c.iloc[-2] - 1) * 100) if len(c) > 1 else None,
        "ema20": _last(ind.ema(c, 20)), "ema50": _last(ind.ema(c, 50)),
        "ema200": _last(ind.ema(c, 200)),
        "rsi14": _last(ind.rsi(c)),
        "macd": _last(m["macd"]), "macd_signal": _last(m["signal"]), "macd_hist": _last(m["hist"]),
        "adx14": _last(a["adx"]), "plus_di": _last(a["plus_di"]), "minus_di": _last(a["minus_di"]),
        "atr14": _last(ind.atr(df)),
        "rel_volume_20": _last(ind.relative_volume(v)),
        "high_20d": _r(df["high"].iloc[-21:-1].max()) if len(df) > 20 else None,
        "low_20d": _r(df["low"].iloc[-21:-1].min()) if len(df) > 20 else None,
        "high_52w": _r(df["high"].iloc[-252:].max()) if len(df) >= 252 else None,
        "low_52w": _r(df["low"].iloc[-252:].min()) if len(df) >= 252 else None,
        "avg_traded_value_cr_20d": _r((c * v).iloc[-20:].mean() / 1e7) if len(df) >= 20 else None,
        "prev_day_high": _r(df["high"].iloc[-2]) if len(df) > 1 else None,
        "prev_day_low": _r(df["low"].iloc[-2]) if len(df) > 1 else None,
    }
    if len(df) > 1:
        p = df.iloc[-2]
        out["pivots_from_prev_bar"] = {k: _r(x) for k, x in
                                       ind.pivot_levels(p["high"], p["low"], p["close"]).items()}
    out["rel_strength_vs_nifty_60d_pp"] = (
        _r(ind.relative_strength(c, bench["close"], 60)) if bench is not None else None)
    out["unavailable"] = [k for k, x in out.items() if x is None]
    return out


def intraday_summary(df: pd.DataFrame) -> dict:
    """df: intraday bars (e.g. 5m) covering at least the latest session."""
    c = df["close"]
    last_day = df.index[-1].date()
    today = df[df.index.date == last_day]
    st = ind.supertrend(df)
    m = ind.macd(c)
    n_or = 3 if len(today) >= 3 else None  # first 3 bars = 15 min on 5m data
    out = {
        "session_date": str(last_day),
        "bars_today": len(today),
        "last": _last(c),
        "vwap": _last(ind.session_vwap(df)),
        "ema9": _last(ind.ema(c, 9)), "ema20": _last(ind.ema(c, 20)),
        "rsi14": _last(ind.rsi(c)),
        "macd_hist": _last(m["hist"]),
        "supertrend": _last(st["supertrend"]),
        "supertrend_direction": {1: "up", -1: "down"}.get(int(st["direction"].iloc[-1])),
        "day_high": _r(today["high"].max()), "day_low": _r(today["low"].min()),
        "opening_range_high": _r(today["high"].iloc[:n_or].max()) if n_or else None,
        "opening_range_low": _r(today["low"].iloc[:n_or].min()) if n_or else None,
        "opening_range_note": "first 3 bars of the session (15 min on 5m bars)",
        "rel_volume_vs_prev_20_bars": _last(ind.relative_volume(df["volume"])),
    }
    out["unavailable"] = [k for k, x in out.items() if x is None]
    return out


# ---------------------------------------------------------------- fundamentals
_INFO_MAP = {
    "sector": "sector", "industry": "industry", "market_cap": "marketCap",
    "pe_trailing": "trailingPE", "pe_forward": "forwardPE", "pb": "priceToBook",
    "peg": "trailingPegRatio", "ev_ebitda": "enterpriseToEbitda",
    "eps_trailing": "trailingEps", "roe": "returnOnEquity",
    "debt_to_equity": "debtToEquity", "operating_margin": "operatingMargins",
    "revenue_growth_yoy": "revenueGrowth", "earnings_growth_yoy": "earningsGrowth",
    "free_cash_flow": "freeCashflow", "operating_cash_flow": "operatingCashflow",
    "insider_holding_pct": "heldPercentInsiders",
    "institutional_holding_pct": "heldPercentInstitutions",
}
NOT_IN_FEED = ["roce", "promoter_holding", "promoter_pledging", "historical_valuation_bands",
               "industry_valuation_comparison", "multi_year_growth_rates", "delivery_volume"]


def fundamentals(symbol: str) -> dict:
    info = data.get_info(symbol)
    out = {k: (info.get(src) if isinstance(info.get(src), str) else _r(info.get(src), 4))
           for k, src in _INFO_MAP.items()}
    missing = [k for k, x in out.items() if x is None]
    return {
        "symbol": data.resolve(symbol),
        "fields": out,
        "unavailable_from_feed": missing + NOT_IN_FEED,
        "notes": [
            "Yahoo fundamentals for Indian stocks are often stale, incomplete or in mixed "
            "units (debt_to_equity is usually a percentage; ratios like roe are fractions). "
            "Cross-check with Screener.in, the annual report or exchange filings.",
            "insider_holding_pct is NOT the same as NSE promoter holding.",
            "Fields under unavailable_from_feed must be researched via web search or "
            "reported as unavailable. Do not estimate them.",
        ],
        "fetched_at": data.now_ist(),
    }


# --------------------------------------------------------------------- scoring
WEIGHTS = {"trend": 20, "price_action": 20, "volume": 15, "sector_market": 15,
           "momentum": 10, "liquidity": 10, "risk_reward": 10}
MIN_SCORE_PCT = 70      # shortlist threshold, on points actually scorable
MIN_COVERAGE = 80       # at least this many of the 100 points must be scorable


def score_setup(df: pd.DataFrame, bench: pd.DataFrame | None, direction: str = "long",
                entry: float | None = None, stop: float | None = None,
                target: float | None = None) -> dict:
    """Spec section 8 score. Components that cannot be computed are None, never guessed."""
    long = direction.lower() in ("long", "buy")
    s = daily_summary(df, bench)
    sign = 1 if long else -1
    comp: dict[str, float | None] = {}
    why: dict[str, str] = {}

    def gt(a, b):  # direction-aware "a is on the favourable side of b"
        return (a - b) * sign > 0

    # Trend (20)
    if None in (s["close"], s["ema20"], s["ema50"], s["ema200"], s["adx14"]):
        comp["trend"] = None
        why["trend"] = "needs 200+ daily bars for EMA200/ADX"
    else:
        adx_pts = 5 if s["adx14"] >= 25 else 2 if s["adx14"] >= 20 else 0
        comp["trend"] = (5 * gt(s["close"], s["ema20"]) + 5 * gt(s["ema20"], s["ema50"])
                         + 5 * gt(s["ema50"], s["ema200"]) + adx_pts)
        why["trend"] = f"close/EMA20/EMA50/EMA200 alignment, ADX {s['adx14']}"

    # Price action (20)
    if len(df) < 21 or s["ema20"] is None:
        comp["price_action"] = None
        why["price_action"] = "needs 21+ daily bars"
    else:
        c = s["close"]
        ref = s["high_20d"] if long else s["low_20d"]
        near_break = abs(c - ref) / ref <= 0.03 or gt(c, ref)
        pullback = abs(c - s["ema20"]) / s["ema20"] <= 0.02 and gt(s["ema20"], s["ema50"] or s["ema20"])
        setup = 8 if (near_break or pullback) else 0
        lo_now, lo_prev = df["low"].iloc[-10:].min(), df["low"].iloc[-20:-10].min()
        hi_now, hi_prev = df["high"].iloc[-10:].max(), df["high"].iloc[-20:-10].max()
        structure = 6 if (lo_now > lo_prev if long else hi_now < hi_prev) else 0
        rng = (df["high"] - df["low"]).iloc[-3:]
        pos = ((df["close"] - df["low"]).iloc[-3:] / rng.where(rng > 0)).mean()
        closes = 0 if pd.isna(pos) else 6 if (pos >= 0.5 if long else pos <= 0.5) else 0
        comp["price_action"] = setup + structure + closes
        why["price_action"] = (f"breakout/pullback location {setup}/8, swing structure "
                               f"{structure}/6, close location {closes}/6")

    # Volume (15)
    rv = s["rel_volume_20"]
    if rv is None or (df["volume"].iloc[-20:] == 0).all():
        comp["volume"] = None
        why["volume"] = "no usable volume (indices on Yahoo often report zero)"
    else:
        comp["volume"] = 15 if rv >= 1.5 else 10 if rv >= 1.2 else 6 if rv >= 1.0 else 2
        why["volume"] = f"latest volume {rv}x the 20-day average"

    # Sector and market (15) - sector index mapping is not available from the feed,
    # so this uses Nifty trend plus relative strength vs Nifty as the proxy.
    if bench is None or len(bench) < 50 or s["rel_strength_vs_nifty_60d_pp"] is None:
        comp["sector_market"] = None
        why["sector_market"] = "benchmark data unavailable"
    else:
        b = bench["close"]
        b50, b20 = ind.ema(b, 50).iloc[-1], ind.ema(b, 20).iloc[-1]
        rs = s["rel_strength_vs_nifty_60d_pp"]
        comp["sector_market"] = (5 * gt(b.iloc[-1], b50) + 3 * gt(b.iloc[-1], b20)
                                 + 7 * (rs * sign > 0))
        why["sector_market"] = (f"Nifty vs its EMA20/EMA50; 60d relative strength {rs} pp "
                                "(sector index NOT included - check separately)")

    # Momentum (10)
    if s["rsi14"] is None or s["macd_hist"] is None:
        comp["momentum"] = None
        why["momentum"] = "needs 35+ daily bars"
    else:
        r = s["rsi14"]
        rsi_ok = 50 <= r <= 70 if long else 30 <= r <= 50
        comp["momentum"] = 5 * rsi_ok + 5 * (s["macd_hist"] * sign > 0)
        why["momentum"] = f"RSI {r}, MACD histogram {s['macd_hist']}"

    # Liquidity (10)
    tv = s["avg_traded_value_cr_20d"]
    if tv is None or tv == 0:
        comp["liquidity"] = None
        why["liquidity"] = "traded value unavailable"
    else:
        comp["liquidity"] = 10 if tv >= 100 else 7 if tv >= 25 else 4 if tv >= 5 else 0
        why["liquidity"] = f"20-day average traded value about Rs {tv} crore"

    # Risk-to-reward (10)
    rr = None
    if None in (entry, stop, target):
        comp["risk_reward"] = None
        why["risk_reward"] = "entry, stop and target not supplied"
    else:
        risk, reward = (entry - stop) * sign, (target - entry) * sign
        if risk <= 0 or reward <= 0:
            comp["risk_reward"] = 0
            why["risk_reward"] = "stop/target are on the wrong side of entry for this direction"
        else:
            rr = reward / risk
            comp["risk_reward"] = 10 if rr >= 3 else 7 if rr >= 2 else 4 if rr >= 1.5 else 0
            why["risk_reward"] = f"reward:risk {rr:.2f}"

    scored = {k: v for k, v in comp.items() if v is not None}
    coverage = sum(WEIGHTS[k] for k in scored)
    points = float(sum(scored.values()))
    pct = round(points / coverage * 100, 1) if coverage else None
    complete = coverage == 100
    return {
        "direction": "long" if long else "short",
        "components": {k: {"points": comp[k], "max": WEIGHTS[k], "basis": why[k]} for k in WEIGHTS},
        "points_scored": points,
        "points_scorable": coverage,
        "score_out_of_100": points if complete else None,
        "score_pct_of_scorable": pct,
        "unscored_components": [k for k in WEIGHTS if comp[k] is None],
        "passes_threshold": bool(pct is not None and coverage >= MIN_COVERAGE and pct >= MIN_SCORE_PCT),
        "threshold": f">= {MIN_SCORE_PCT}% of scorable points with >= {MIN_COVERAGE} points scorable",
        "reward_to_risk": _r(rr),
        "technicals": s,
        "note": ("Score is a rule-based screen, not a probability of profit. "
                 "Unscored components are missing data and were not estimated."),
    }
