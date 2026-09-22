"""Rank a universe into intraday long/short candidates and build a trade plan.

The scoring blends the signals a discretionary intraday trader eyeballs each
morning -- relative volume, price vs VWAP, RSI, ATR, gap %, 20/50 EMA
alignment, position within the recent range (breakout proximity), previous-day
high/low, relative strength vs the index, and (optionally) sector strength --
into a single 0-100 bull score and its mirror bear score, so the same universe
can be ranked for longs and for shorts in one pass.

Everything here is a transparent, fixed heuristic, NOT an empirically validated
edge and NOT investment advice. The sub-scores and weights are documented so a
reader can see exactly what each number means; validate any of it against your
own results before trading it. Two honesty notes that matter for reading the
output:

* VWAP here is the app's 20-session rolling VWAP from *daily* bars, not a true
  single-session intraday VWAP (which needs intraday bars). It is a slower,
  swing-oriented reference line, and the caller should label it as such.
* Relative volume can be time-adjusted via ``session_fraction`` so a partial
  day's volume is projected to a full-day figure before comparing it to the
  20-day average; pass 1.0 (the default) once the session is complete.
"""

from __future__ import annotations

import pandas as pd

# Weights for the directional sub-scores; must sum to 1.0. These are a
# reasonable starting point, not a tuned/validated set.
WEIGHTS: dict[str, float] = {
    "vwap": 0.20,
    "ema": 0.17,
    "rsi": 0.13,
    "gap": 0.10,
    "range": 0.17,     # position within the recent range == breakout proximity
    "rs": 0.13,        # relative strength vs the benchmark index
    "relvol": 0.10,    # non-directional conviction (helps long and short alike)
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "WEIGHTS must sum to 1.0"

# How much a supplied sector-strength reading tilts the final score (0..1).
SECTOR_TILT = 0.08


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, v)))


def _last(series: pd.Series | None):
    if series is None:
        return None
    s = series.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _ema(close: pd.Series, span: int) -> float | None:
    c = close.dropna()
    if len(c) < span:
        return None
    return float(c.ewm(span=span, adjust=False).mean().iloc[-1])


def _atr(high: pd.Series | None, low: pd.Series | None, close: pd.Series,
         period: int = 14) -> float | None:
    """Average true range in price terms (simple mean of the true range,
    matching the app's existing ATR% helper)."""
    if high is None or low is None or close is None:
        return None
    df = pd.DataFrame({"h": high, "l": low, "c": close}).dropna()
    if len(df) < period + 1:
        return None
    pc = df["c"].shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - pc).abs(),
                    (df["l"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None


def _rolling_vwap(close: pd.Series, vol: pd.Series | None, n: int = 20) -> float | None:
    if vol is None:
        return None
    df = pd.concat([close, vol], axis=1).dropna()
    if df.empty:
        return None
    df = df.tail(n)
    tot = float(df.iloc[:, 1].sum())
    if not tot:
        return None
    return float((df.iloc[:, 0] * df.iloc[:, 1]).sum() / tot)


def intraday_features(open_: pd.Series | None, high: pd.Series | None,
                      low: pd.Series | None, close: pd.Series,
                      vol: pd.Series | None, rsi: float | None = None,
                      rs_score: float | None = None,
                      session_fraction: float = 1.0) -> dict | None:
    """Derive every raw feature the scorer needs from one symbol's daily OHLCV
    history (with today's forming bar included while the market is open).

    ``rsi`` and ``rs_score`` (relative-strength-vs-index, 0-100) can be passed
    in when the caller has already computed them for other columns, to avoid
    recomputing; both are optional. Returns None if there isn't enough history.
    """
    c = close.dropna() if close is not None else None
    if c is None or len(c) < 21:
        return None
    price = float(c.iloc[-1])
    if price <= 0:
        return None

    prev_close = float(c.iloc[-2])
    o = open_.dropna() if open_ is not None else None
    today_open = float(o.iloc[-1]) if o is not None and not o.empty else None
    gap_pct = ((today_open / prev_close - 1) * 100) if (today_open and prev_close) else None

    h = high.dropna() if high is not None else None
    l = low.dropna() if low is not None else None
    prev_high = float(h.iloc[-2]) if h is not None and len(h) >= 2 else None
    prev_low = float(l.iloc[-2]) if l is not None and len(l) >= 2 else None

    # Recent range for breakout proximity. Use the prior 20 completed sessions
    # (exclude today's forming bar) so "near the high" means near a real prior
    # extreme rather than near today's own high.
    hi20 = float(h.iloc[-21:-1].max()) if h is not None and len(h) >= 21 else None
    lo20 = float(l.iloc[-21:-1].min()) if l is not None and len(l) >= 21 else None
    if hi20 is None or lo20 is None:
        win = c.iloc[-21:-1]
        hi20 = hi20 if hi20 is not None else float(win.max())
        lo20 = lo20 if lo20 is not None else float(win.min())
    rng = hi20 - lo20
    range_pos = _clamp((price - lo20) / rng, 0.0, 1.0) if rng > 0 else 0.5

    v = vol.dropna() if vol is not None else None
    last_vol = float(v.iloc[-1]) if v is not None and not v.empty else None
    avg20_vol = float(v.iloc[-21:-1].mean()) if v is not None and len(v) >= 21 else None
    rel_vol = None
    if last_vol and avg20_vol:
        frac = session_fraction if 0.05 <= session_fraction <= 1.0 else 1.0
        rel_vol = (last_vol / frac) / avg20_vol

    atr = _atr(h, l, c)
    return {
        "price": price,
        "prev_close": prev_close,
        "gap_pct": gap_pct,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "hi20": hi20,
        "lo20": lo20,
        "range_pos": range_pos,
        "vwap": _rolling_vwap(c, v),
        "ema20": _ema(c, 20),
        "ema50": _ema(c, 50),
        "rsi": rsi,
        "rs_score": rs_score,
        "atr": atr,
        "atr_pct": (atr / price * 100) if atr else None,
        "rel_vol": rel_vol,
    }


def sub_scores(feat: dict) -> dict:
    """Directional 0-100 sub-scores (50 == neutral). Higher == more bullish,
    except ``relvol`` which is a non-directional conviction score."""
    price = feat["price"]

    vwap = feat.get("vwap")
    s_vwap = _clamp(50 + (price / vwap - 1) * 100 / 3.0 * 50) if vwap else 50.0

    ema20, ema50 = feat.get("ema20"), feat.get("ema50")
    if ema20 and ema50:
        a = (price / ema20 - 1) * 100      # price above/below 20 EMA (%)
        b = (ema20 / ema50 - 1) * 100      # 20 EMA above/below 50 EMA (%)
        s_ema = _clamp(50 + _clamp(a / 3.0 * 30, -30, 30) + _clamp(b / 3.0 * 20, -20, 20))
    elif ema20:
        a = (price / ema20 - 1) * 100
        s_ema = _clamp(50 + _clamp(a / 3.0 * 50, -50, 50))
    else:
        s_ema = 50.0

    rsi = feat.get("rsi")
    s_rsi = _clamp(50 + (rsi - 50) * 2.0) if rsi is not None else 50.0

    gap = feat.get("gap_pct")
    s_gap = _clamp(50 + _clamp(gap / 3.0 * 50, -50, 50)) if gap is not None else 50.0

    s_range = _clamp(feat.get("range_pos", 0.5) * 100)

    rs = feat.get("rs_score")
    s_rs = _clamp(rs) if rs is not None else 50.0

    relvol = feat.get("rel_vol")
    s_relvol = _clamp(50 + _clamp((relvol - 1) * 40, -40, 45)) if relvol else 50.0

    return {"vwap": s_vwap, "ema": s_ema, "rsi": s_rsi, "gap": s_gap,
            "range": s_range, "rs": s_rs, "relvol": s_relvol}


def score_candidate(feat: dict, sector_strength: float | None = None) -> dict:
    """Blend the sub-scores into a bull score and its mirror bear score, both
    0-100. ``relvol`` adds conviction to *both* sides (it is not inverted for
    the bear score). An optional ``sector_strength`` (0-100) tilts the result.
    """
    s = sub_scores(feat)
    bull = sum(WEIGHTS[k] * s[k] if k != "relvol" else 0.0 for k in WEIGHTS)
    bear = sum(WEIGHTS[k] * (100 - s[k]) if k != "relvol" else 0.0 for k in WEIGHTS)
    # relvol helps both directions (same, un-inverted term)
    bull += WEIGHTS["relvol"] * s["relvol"]
    bear += WEIGHTS["relvol"] * s["relvol"]

    if sector_strength is not None:
        bull = (1 - SECTOR_TILT) * bull + SECTOR_TILT * _clamp(sector_strength)
        bear = (1 - SECTOR_TILT) * bear + SECTOR_TILT * (100 - _clamp(sector_strength))

    return {"bull": round(_clamp(bull), 1), "bear": round(_clamp(bear), 1),
            "sub": {k: round(v, 1) for k, v in s.items()}}


def trade_plan(feat: dict, side: str) -> dict | None:
    """ATR- and structure-anchored plan for one side ("long" or "short").

    The stop (invalidation) is placed at the nearest structural level beyond
    the entry -- VWAP, the 20 EMA, or the previous day's extreme -- but never
    wider than 1.5x ATR. Risk R is (entry - stop); the 2R and 3R targets are
    that same R projected in the trade's direction. These are mechanical levels
    for planning, not price predictions or advice.
    """
    price = feat.get("price")
    if not price:
        return None
    atr = feat.get("atr") or price * 0.01  # fall back to 1% if ATR unavailable
    vwap, ema20 = feat.get("vwap"), feat.get("ema20")
    prev_high, prev_low = feat.get("prev_high"), feat.get("prev_low")

    if side == "long":
        supports = [x for x in (vwap, ema20, prev_low) if x is not None and x < price]
        struct = max(supports) if supports else None
        sl_struct = (struct - 0.15 * atr) if struct is not None else float("-inf")
        stop = max(sl_struct, price - 1.5 * atr)
        if stop >= price:
            stop = price - 1.5 * atr
        risk = price - stop
        return {
            "side": "long",
            "entry_low": round(price - 0.20 * atr, 2),
            "entry_high": round(price + 0.10 * atr, 2),
            "entry_ref": round(price, 2),
            "stop": round(stop, 2),
            "risk": round(risk, 2),
            "target_2r": round(price + 2 * risk, 2),
            "target_3r": round(price + 3 * risk, 2),
        }
    if side == "short":
        resistances = [x for x in (vwap, ema20, prev_high) if x is not None and x > price]
        struct = min(resistances) if resistances else None
        sl_struct = (struct + 0.15 * atr) if struct is not None else float("inf")
        stop = min(sl_struct, price + 1.5 * atr)
        if stop <= price:
            stop = price + 1.5 * atr
        risk = stop - price
        return {
            "side": "short",
            "entry_low": round(price - 0.10 * atr, 2),
            "entry_high": round(price + 0.20 * atr, 2),
            "entry_ref": round(price, 2),
            "stop": round(stop, 2),
            "risk": round(risk, 2),
            "target_2r": round(price - 2 * risk, 2),
            "target_3r": round(price - 3 * risk, 2),
        }
    raise ValueError(f"side must be 'long' or 'short', got {side!r}")


def build_candidate(symbol: str, feat: dict, sector: str | None = None,
                    sector_strength: float | None = None) -> dict:
    """One symbol's full record: scores plus both trade plans, ready to rank."""
    sc = score_candidate(feat, sector_strength)
    return {
        "symbol": symbol,
        "sector": sector,
        "sector_strength": sector_strength,
        "bull": sc["bull"],
        "bear": sc["bear"],
        "sub": sc["sub"],
        "feat": feat,
        "long_plan": trade_plan(feat, "long"),
        "short_plan": trade_plan(feat, "short"),
    }


def rank(candidates: list[dict], top_n: int = 5,
         min_atr_pct: float | None = None) -> dict:
    """Split a list of ``build_candidate`` records into the top ``top_n`` longs
    (by bull score) and top ``top_n`` shorts (by bear score).

    ``min_atr_pct`` optionally drops names too quiet to be worth trading
    intraday (their ATR% is below the floor)."""
    pool = candidates
    if min_atr_pct is not None:
        pool = [c for c in pool
                if (c["feat"].get("atr_pct") or 0) >= min_atr_pct]
    longs = sorted(pool, key=lambda c: c["bull"], reverse=True)[:top_n]
    shorts = sorted(pool, key=lambda c: c["bear"], reverse=True)[:top_n]
    return {"longs": longs, "shorts": shorts}
