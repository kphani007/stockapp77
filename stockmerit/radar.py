"""StockMerit Master Radar: repeatable broad-universe technical/fundamental scan.

The model is deliberately fixed. Market data changes every run; the scoring methodology
does not. Missing fundamental data is treated as missing, not as an artificial neutral
score, and the output exposes data coverage so a high score cannot hide weak evidence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

from stockmerit.features.momentum import rolling_rsi
from stockmerit.features.relative_strength import relative_strength_score


WEIGHTS = {
    "trend": 0.20,
    "momentum": 0.10,
    "rs": 0.20,
    "volume": 0.10,
    "breakout": 0.10,
    "risk": 0.05,
    "fund": 0.25,
}


def clip(x, lo=0, hi=100):
    return float(max(lo, min(hi, x)))


def fund_score(info):
    """Score fundamental quality/growth/value on a 0-100 scale.

    Only available metrics contribute. This avoids silently treating unavailable
    fundamentals as a genuine neutral 50.
    """
    vals = []

    revenue = info.get("revenueGrowth")
    earnings = info.get("earningsGrowth")
    roe = info.get("returnOnEquity")
    debt = info.get("debtToEquity")
    fcf = info.get("freeCashflow")
    ocf = info.get("operatingCashflow")
    peg = info.get("pegRatio") or info.get("trailingPegRatio")
    pe = info.get("trailingPE")

    if revenue is not None:
        vals.append(clip(50 + float(revenue) * 200))
    if earnings is not None:
        vals.append(clip(50 + float(earnings) * 180))
    if roe is not None:
        vals.append(clip(float(roe) * 200))
    if debt is not None:
        vals.append(clip(90 - float(debt) * 0.35))

    if fcf is not None:
        vals.append(75 if float(fcf) > 0 else 25)
    if ocf is not None:
        vals.append(75 if float(ocf) > 0 else 25)

    if peg is not None and float(peg) > 0:
        vals.append(clip(100 - (float(peg) - 0.8) * 30))
    elif pe is not None and float(pe) > 0:
        vals.append(clip(75 - max(float(pe) - 25, 0) * 1.2))

    return round(float(np.mean(vals)), 1) if vals else None


def tech_row(sym, df, benchmark):
    if df is None or df.empty or "Close" not in df:
        return None

    c = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(c) < 210:
        return None

    v = pd.to_numeric(
        df.get("Volume", pd.Series(index=df.index, dtype=float)),
        errors="coerce",
    ).fillna(0)
    h = pd.to_numeric(df.get("High", c), errors="coerce")
    l = pd.to_numeric(df.get("Low", c), errors="coerce")

    p = float(c.iloc[-1])
    s20 = float(c.rolling(20).mean().iloc[-1])
    s50 = float(c.rolling(50).mean().iloc[-1])
    s200 = float(c.rolling(200).mean().iloc[-1])
    rsi = float(rolling_rsi(c).iloc[-1])

    v20 = float(v.tail(20).mean()) or 0
    rv = float(v.iloc[-1] / v20) if v20 else 0

    h20 = float(c.tail(20).max())
    h52 = float(c.tail(252).max())

    trend = (
        (30 if p > s50 else 0)
        + (30 if p > s200 else 0)
        + (20 if s50 > s200 else 0)
        + (20 if s20 > s50 else 0)
    )

    # Reward constructive momentum but avoid giving an extreme RSI a free pass.
    momentum = clip(
        50 + (rsi - 50) * 1.6
        - (min(15, (rsi - 75) * 1.5) if rsi > 75 else 0)
    )

    rs = relative_strength_score(c, benchmark, min(63, len(c) - 1))
    rs = 50 if rs is None else rs

    volume = clip(50 + (rv - 1) * 35)

    d20 = (h20 / p - 1) * 100 if p else 99
    d52 = (h52 / p - 1) * 100 if p else 99

    breakout = clip(100 - d20 * 8)
    if rv >= 1.5 and d20 <= 3:
        breakout = clip(breakout + 12)

    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1] / p * 100) if p else 99

    # Higher ATR is riskier; being moderately above the 200DMA is not itself a risk.
    risk = clip(90 - max(0, atr - 2) * 12)

    ret63 = float(c.iloc[-1] / c.iloc[-64] - 1) * 100 if len(c) > 64 else None

    return {
        "Symbol": sym.replace(".NS", ""),
        "Price": round(p, 2),
        "RSI": round(rsi, 1),
        "SMA20": round(s20, 2),
        "SMA50": round(s50, 2),
        "SMA200": round(s200, 2),
        "RelVol": round(rv, 2),
        "From20DHigh%": round(d20, 2),
        "From52WHigh%": round(d52, 2),
        "Return63D%": round(ret63, 2) if ret63 is not None else None,
        "ATR14%": round(atr, 2),
        "_trend": trend,
        "_momentum": momentum,
        "_rs": rs,
        "_volume": volume,
        "_breakout": breakout,
        "_risk": risk,
    }


def state(score):
    if score >= 75:
        return "Core Candidate"
    if score >= 65:
        return "Emerging"
    if score >= 55:
        return "Watch"
    return "Weak"


def _download_fundamentals(symbols):
    """Fetch fundamentals with one isolated failure per symbol."""
    result = {}
    for symbol in symbols:
        try:
            info = yf.Ticker(symbol + ".NS").info or {}
            score = fund_score(info)
            result[symbol] = (score, info)
        except Exception:
            result[symbol] = (None, {})
    return result


def scan_master_radar(tickers, fund_limit=200, batch=200):
    """Scan a broad NSE universe and return a deterministic score-sorted dataframe."""
    if not tickers:
        return pd.DataFrame()

    benchmark_data = yf.download(
        "^NSEI",
        period="2y",
        interval="1d",
        auto_adjust=False,
        progress=False,
    )
    if benchmark_data.empty:
        return pd.DataFrame()

    benchmark = (
        benchmark_data["Close"].squeeze()
        if isinstance(benchmark_data.columns, pd.MultiIndex)
        else benchmark_data["Close"]
    ).dropna()

    rows = []
    for i in range(0, len(tickers), batch):
        chunk = tickers[i : i + batch]
        data = yf.download(
            chunk,
            period="2y",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
        )

        for sym in chunk:
            try:
                df = data[sym] if isinstance(data.columns, pd.MultiIndex) else data
                row = tech_row(sym, df, benchmark)
                if row:
                    rows.append(row)
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()

    d = pd.DataFrame(rows)

    # Technical pre-screen: fundamentals are enriched only for the strongest
    # technical candidates to keep the broad scan practical.
    d["_pre"] = (
        d["_trend"] * 0.25
        + d["_momentum"] * 0.12
        + d["_rs"] * 0.25
        + d["_volume"] * 0.13
        + d["_breakout"] * 0.18
        + d["_risk"] * 0.07
    )

    fund_symbols = (
        d.sort_values("_pre", ascending=False)["Symbol"]
        .head(max(1, int(fund_limit)))
        .tolist()
    )
    fmap = _download_fundamentals(fund_symbols)

    out = []
    for _, row in d.iterrows():
        symbol = row["Symbol"]
        fs, info = fmap.get(symbol, (None, {}))

        # Do not award an invented 50 when fundamentals are unavailable.
        # Re-normalize the final weights over the available components.
        components = {
            "trend": row["_trend"],
            "momentum": row["_momentum"],
            "rs": row["_rs"],
            "volume": row["_volume"],
            "breakout": row["_breakout"],
            "risk": row["_risk"],
        }
        if fs is not None:
            components["fund"] = fs

        weight_total = sum(WEIGHTS[k] for k in components)
        score = sum(components[k] * WEIGHTS[k] for k in components) / weight_total

        q = row.to_dict()
        q["Radar Score"] = round(float(score), 1)
        q["Status"] = state(score)
        q["Fundamental Score"] = round(fs, 1) if fs is not None else None
        q["Fundamental Coverage"] = "Available" if fs is not None else "Missing"
        q["Sector"] = info.get("sector", "n/a")
        q["Market Cap Cr"] = (
            round(float(info["marketCap"]) / 1e7, 0)
            if info.get("marketCap")
            else None
        )
        q["PE"] = (
            round(float(info["trailingPE"]), 1)
            if info.get("trailingPE")
            else None
        )
        peg_value = info.get("pegRatio") or info.get("trailingPegRatio")
        q["PEG"] = round(float(peg_value), 2) if peg_value else None
        out.append(q)

    result = pd.DataFrame(out)
    return result.sort_values(
        ["Radar Score", "_rs", "Return63D%", "Symbol"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
