"""Tool schemas exposed to the model and the dispatcher that runs them."""
from __future__ import annotations

import json
from pathlib import Path

from . import analysis, backtest, data, options, profile, risk

CONFIG = Path(__file__).resolve().parent / "config"
NO_GUESS = "Data unavailable. Report it as unavailable; do not estimate or invent a value."


def _bench():
    try:
        return data.get_history("NIFTY", "2y", "1d")
    except data.DataUnavailable:
        return None


def market_snapshot(include_sectors: bool = True) -> dict:
    rows, failed = {}, {}
    for name in data.SNAPSHOT + (data.SECTORS if include_sectors else []):
        try:
            df = data.get_history(name, "1mo", "1d")
            c = df["close"]
            rows[name] = {
                "last_close": round(float(c.iloc[-1]), 2),
                "change_pct": round(float((c.iloc[-1] / c.iloc[-2] - 1) * 100), 2) if len(c) > 1 else None,
                "change_5d_pct": round(float((c.iloc[-1] / c.iloc[-6] - 1) * 100), 2) if len(c) > 5 else None,
                "last_bar_time": str(df.index[-1]),
            }
        except data.DataUnavailable as e:
            failed[name] = str(e)
    trends = {}
    for name in ("NIFTY", "BANKNIFTY"):
        try:
            s = analysis.daily_summary(data.get_history(name, "2y", "1d"))
            trends[name] = {k: s[k] for k in ("close", "ema20", "ema50", "ema200", "rsi14", "adx14",
                                              "prev_day_high", "prev_day_low", "pivots_from_prev_bar")}
        except data.DataUnavailable as e:
            failed[name + "_trend"] = str(e)
    return {
        "instruments": rows, "index_trend": trends, "failed": failed,
        "not_available_from_this_feed": [
            "FII/DII cash and derivatives activity", "market breadth (advances/declines)",
            "GIFT Nifty", "option chain / OI / PCR / max pain", "delivery volume",
            "corporate announcements and earnings calendar"],
        "how_to_fill_gaps": "Use web_search against NSE/BSE/SEBI/RBI or major financial "
                            "media, cite the source and its timestamp, or state unavailable.",
        "fetched_at": data.now_ist(), "data_quality": data.DATA_QUALITY,
    }


def technical_analysis(symbol: str, timeframe: str = "daily") -> dict:
    tf = {"daily": ("2y", "1d"), "weekly": ("5y", "1wk"),
          "intraday_5m": ("5d", "5m"), "intraday_15m": ("1mo", "15m")}
    if timeframe not in tf:
        return {"error": f"timeframe must be one of {list(tf)}"}
    df = data.get_history(symbol, *tf[timeframe])
    if timeframe.startswith("intraday"):
        body = analysis.intraday_summary(df)
    else:
        body = analysis.daily_summary(df, _bench() if timeframe == "daily" else None)
    return {"symbol": data.resolve(symbol), "timeframe": timeframe, **body, **data.stamp(df)}


def score(symbol: str, direction: str = "long", entry: float | None = None,
          stop: float | None = None, target: float | None = None) -> dict:
    df = data.get_history(symbol, "2y", "1d")
    out = analysis.score_setup(df, _bench(), direction, entry, stop, target)
    return {"symbol": data.resolve(symbol), **out, **data.stamp(df)}


def scan(symbols: list[str] | None = None, direction: str = "long", top_n: int = 10) -> dict:
    src = "caller-supplied list"
    if not symbols:
        symbols = json.loads((CONFIG / "universe.json").read_text())["symbols"]
        src = "config/universe.json (editable watchlist, NOT official index constituents)"
    symbols = symbols[:80]
    bench, rows, failed = _bench(), [], {}
    for sym in symbols:
        try:
            df = data.get_history(sym, "2y", "1d")
            s = analysis.score_setup(df, bench, direction)
            t = s["technicals"]
            rows.append({"symbol": sym, "score_pct_of_scorable": s["score_pct_of_scorable"],
                         "points_scored": s["points_scored"], "points_scorable": s["points_scorable"],
                         "unscored": s["unscored_components"], "close": t["close"],
                         "rsi14": t["rsi14"], "adx14": t["adx14"], "rel_volume_20": t["rel_volume_20"],
                         "rel_strength_vs_nifty_60d_pp": t["rel_strength_vs_nifty_60d_pp"],
                         "last_bar_time": str(df.index[-1])})
        except data.DataUnavailable as e:
            failed[sym] = str(e)
    rows.sort(key=lambda r: (r["score_pct_of_scorable"] is not None, r["score_pct_of_scorable"] or 0),
              reverse=True)
    return {
        "universe": src, "direction": direction, "scanned": len(rows), "failed": failed,
        "top": rows[:top_n],
        "note": "Pre-screen only: risk_reward is unscored here (max 90 scorable). Re-score each "
                "candidate with entry/stop/target before shortlisting. A high rank is not a "
                "recommendation.",
        "fetched_at": data.now_ist(), "data_quality": data.DATA_QUALITY,
    }


def run_backtest(symbol: str, strategy: str = "ema_pullback", years: int = 10, **kw) -> dict:
    df = data.get_history(symbol, f"{min(max(years, 2), 20)}y", "1d")
    return {"symbol": data.resolve(symbol), **backtest.run(df, strategy, **kw), **data.stamp(df)}


def _num(desc):
    return {"type": "number", "description": desc}


TOOLS = [
    {"name": "get_market_snapshot",
     "description": "Latest daily closes and % change for Nifty, Bank Nifty, Sensex, India VIX, USD/INR, "
                    "Brent, gold, global indices and NSE sector indices, plus Nifty/Bank Nifty trend "
                    "levels. Delayed data. Does NOT include FII/DII, breadth, GIFT Nifty or option chain.",
     "input_schema": {"type": "object", "properties": {"include_sectors": {"type": "boolean"}}}},
    {"name": "get_technical_analysis",
     "description": "Indicator summary for an NSE symbol or index alias (NIFTY, BANKNIFTY...). daily/weekly: "
                    "EMA20/50/200, RSI, MACD, ADX, ATR, relative volume, 20d/52w levels, pivots+CPR, "
                    "relative strength vs Nifty. intraday_5m/15m: VWAP, EMA9/20, RSI, MACD, Supertrend, "
                    "opening range, day high/low. Delayed data with timestamps.",
     "input_schema": {"type": "object", "required": ["symbol"], "properties": {
         "symbol": {"type": "string", "description": "NSE symbol e.g. RELIANCE, or alias e.g. NIFTY"},
         "timeframe": {"type": "string", "enum": ["daily", "weekly", "intraday_5m", "intraday_15m"]}}}},
    {"name": "get_fundamentals",
     "description": "Valuation and quality fields from Yahoo (P/E, P/B, PEG, EV/EBITDA, ROE, D/E, margins, "
                    "growth, cash flow). Often incomplete for Indian stocks; ROCE, promoter holding and "
                    "pledging are never available here and must be researched via web_search.",
     "input_schema": {"type": "object", "required": ["symbol"],
                      "properties": {"symbol": {"type": "string"}}}},
    {"name": "score_setup",
     "description": "Rule-based 100-point setup score (trend 20, price action 20, volume 15, sector/market "
                    "15, momentum 10, liquidity 10, risk-reward 10). Supply entry/stop/target to score "
                    "risk-reward. Components with missing data are returned unscored, never estimated.",
     "input_schema": {"type": "object", "required": ["symbol"], "properties": {
         "symbol": {"type": "string"}, "direction": {"type": "string", "enum": ["long", "short"]},
         "entry": _num("planned entry price"), "stop": _num("stop-loss price"),
         "target": _num("first target price")}}},
    {"name": "scan_universe",
     "description": "Pre-screen a list of symbols (default: config/universe.json watchlist) with the setup "
                    "score and return the top candidates. Slow for long lists.",
     "input_schema": {"type": "object", "properties": {
         "symbols": {"type": "array", "items": {"type": "string"}},
         "direction": {"type": "string", "enum": ["long", "short"]},
         "top_n": {"type": "integer"}}}},
    {"name": "position_size",
     "description": "Quantity = max rupee risk / |entry - stop|, honouring the daily loss limit. For F&O pass "
                    "a VERIFIED lot_size and per-unit prices. Refuses risk above 2% of capital per trade.",
     "input_schema": {"type": "object", "required": ["capital", "entry", "stop"], "properties": {
         "capital": _num("trading capital in rupees"), "entry": _num("entry price per unit"),
         "stop": _num("stop price per unit"), "risk_pct": _num("percent of capital risked, default 0.5"),
         "lot_size": {"type": "integer", "description": "F&O lot size, verified from NSE/broker"},
         "max_daily_loss": _num("rupee daily loss limit"), "loss_today": _num("rupees already lost today"),
         "leverage": _num("intraday leverage multiple, default 1")}}},
    {"name": "estimate_charges",
     "description": "Round-trip brokerage, STT, exchange, SEBI, stamp duty and GST estimate from "
                    "config/charges.json. Excludes slippage.",
     "input_schema": {"type": "object", "required": ["segment", "buy_price", "sell_price", "quantity"],
                      "properties": {
                          "segment": {"type": "string", "enum": ["delivery", "intraday", "futures", "options"]},
                          "buy_price": _num("per unit"), "sell_price": _num("per unit"),
                          "quantity": _num("units (lots x lot size for F&O)")}}},
    {"name": "option_strategy_payoff",
     "description": "Expiry payoff for a multi-leg option/futures structure: max profit, max loss, breakevens, "
                    "reward:risk, and a warning when a short option is uncovered. Arithmetic only: premiums "
                    "and lot size must be real, sourced values.",
     "input_schema": {"type": "object", "required": ["legs", "lot_size"], "properties": {
         "legs": {"type": "array", "items": {"type": "object", "required": ["type", "side"], "properties": {
             "type": {"type": "string", "enum": ["CE", "PE", "FUT"]},
             "side": {"type": "string", "enum": ["BUY", "SELL"]},
             "strike": {"type": "number"}, "premium": {"type": "number"},
             "price": _num("futures entry price (FUT legs only)"), "ratio": {"type": "number"}}}},
         "lot_size": {"type": "integer"}, "lots": {"type": "integer"}}}},
    {"name": "option_greeks",
     "description": "Black-Scholes theoretical price and Greeks from a caller-supplied IV. Not a market quote.",
     "input_schema": {"type": "object", "required": ["spot", "strike", "days_to_expiry", "iv_pct"],
                      "properties": {"spot": {"type": "number"}, "strike": {"type": "number"},
                                     "days_to_expiry": {"type": "number"}, "iv_pct": {"type": "number"},
                                     "option_type": {"type": "string", "enum": ["CE", "PE"]},
                                     "rate_pct": {"type": "number"}}}},
    {"name": "backtest_strategy",
     "description": "Daily-bar long-only backtest of 'ema_pullback' or 'breakout_20d' on one symbol with costs "
                    "and slippage: win rate, avg win/loss, profit factor, drawdown, earlier/later split, "
                    "per-year results.",
     "input_schema": {"type": "object", "required": ["symbol"], "properties": {
         "symbol": {"type": "string"},
         "strategy": {"type": "string", "enum": list(backtest.STRATEGIES)},
         "years": {"type": "integer"}, "atr_stop_mult": {"type": "number"},
         "reward_r": {"type": "number"}, "max_hold_bars": {"type": "integer"},
         "cost_pct_per_side": {"type": "number"}, "slippage_pct_per_side": {"type": "number"}}}},
    {"name": "get_profile",
     "description": "Read the user's saved trading profile and the list of fields still missing.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "update_profile",
     "description": "Save profile fields the USER has explicitly stated. Never save assumed values.",
     "input_schema": {"type": "object", "required": ["fields"], "properties": {
         "fields": {"type": "object", "description": "Keys from: " + ", ".join(profile.FIELDS)}}}},
]

_DISPATCH = {
    "get_market_snapshot": market_snapshot,
    "get_technical_analysis": technical_analysis,
    "get_fundamentals": analysis.fundamentals,
    "score_setup": score,
    "scan_universe": scan,
    "position_size": risk.position_size,
    "estimate_charges": risk.estimate_charges,
    "option_strategy_payoff": options.strategy_payoff,
    "option_greeks": options.bs_greeks,
    "backtest_strategy": run_backtest,
}


def run_tool(name: str, args: dict, profile_store: dict | None = None,
             extra: dict | None = None) -> str:
    """Execute a tool and return a JSON string. Never raises.

    profile_store: the calling session's profile dict.
    extra: {tool_name: callable} supplied by the host app (e.g. Dhan OI).
    """
    args = args or {}
    try:
        if name == "get_profile":
            return json.dumps(profile.status(profile_store if profile_store is not None else {}))
        if name == "update_profile":
            if profile_store is None:
                return json.dumps({"error": "no profile store in this session"})
            return json.dumps(profile.update(profile_store, args.get("fields") or {}))
        fn = (extra or {}).get(name) or _DISPATCH.get(name)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name}"})
        return json.dumps(fn(**args), default=str)
    except data.DataUnavailable as e:
        return json.dumps({"error": str(e), "instruction": NO_GUESS})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}", "instruction": NO_GUESS})
