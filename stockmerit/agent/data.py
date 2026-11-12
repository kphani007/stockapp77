"""Market data access.

Default provider is Yahoo Finance via yfinance. That feed is UNOFFICIAL and DELAYED
for NSE/BSE, and has no NSE option chain, OI, FII/DII, delivery volume or promoter
data. Every payload built on it is labelled accordingly. To use a broker feed, write a
class with the same two methods (history, info) and call set_provider().
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")

# Yahoo Finance tickers. Sector index tickers are best-effort: if Yahoo does not
# serve one, the tool returns an explicit error instead of a value.
ALIASES = {
    "NIFTY": "^NSEI", "NIFTY50": "^NSEI", "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK", "NIFTY BANK": "^NSEBANK", "NIFTYBANK": "^NSEBANK",
    "SENSEX": "^BSESN",
    "INDIAVIX": "^INDIAVIX", "INDIA VIX": "^INDIAVIX", "VIX": "^INDIAVIX",
    "NIFTYIT": "^CNXIT", "NIFTYAUTO": "^CNXAUTO", "NIFTYPHARMA": "^CNXPHARMA",
    "NIFTYFMCG": "^CNXFMCG", "NIFTYMETAL": "^CNXMETAL", "NIFTYREALTY": "^CNXREALTY",
    "NIFTYENERGY": "^CNXENERGY", "NIFTYPSUBANK": "^CNXPSUBANK",
    "USDINR": "INR=X", "BRENT": "BZ=F", "CRUDE": "BZ=F", "GOLD": "GC=F",
    "SP500": "^GSPC", "NASDAQ": "^IXIC", "NIKKEI": "^N225", "HANGSENG": "^HSI",
}

SNAPSHOT = ["NIFTY", "BANKNIFTY", "SENSEX", "INDIAVIX", "USDINR", "BRENT", "GOLD",
            "SP500", "NASDAQ", "NIKKEI", "HANGSENG"]
SECTORS = ["NIFTYIT", "NIFTYAUTO", "NIFTYPHARMA", "NIFTYFMCG", "NIFTYMETAL",
           "NIFTYREALTY", "NIFTYENERGY", "NIFTYPSUBANK"]

DATA_QUALITY = ("Yahoo Finance via yfinance: unofficial, DELAYED (not real-time) for "
                "NSE/BSE. Verify against your broker terminal before acting.")


def resolve(symbol: str) -> str:
    s = symbol.strip().upper()
    if s in ALIASES:
        return ALIASES[s]
    if s.startswith("^") or "=" in s or s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


class DataUnavailable(Exception):
    pass


class YFinanceProvider:
    name = "yfinance"

    def history(self, ticker: str, period: str, interval: str) -> pd.DataFrame:
        import yfinance as yf  # imported lazily so offline tools/tests work without it
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        return df

    def info(self, ticker: str) -> dict:
        import yfinance as yf
        return yf.Ticker(ticker).info or {}


_provider = YFinanceProvider()
_cache: dict = {}
TTL = {"1d": 300, "1wk": 900}


def set_provider(p) -> None:
    global _provider
    _provider = p
    _cache.clear()


def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def get_history(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """OHLCV with lowercase columns. Raises DataUnavailable rather than returning junk."""
    t = resolve(symbol)
    key = (t, period, interval)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL.get(interval, 60):
        return hit[1]
    try:
        df = _provider.history(t, period, interval)
    except Exception as e:  # network, bad ticker, provider change
        raise DataUnavailable(f"{t}: fetch failed ({type(e).__name__}: {e})") from e
    if df is None or len(df) == 0:
        raise DataUnavailable(f"{t}: provider returned no rows for {period}/{interval}")
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"])
    if len(df) == 0:
        raise DataUnavailable(f"{t}: no valid price rows")
    _cache[key] = (time.time(), df)
    return df


def get_info(symbol: str) -> dict:
    t = resolve(symbol)
    try:
        return _provider.info(t)
    except Exception as e:
        raise DataUnavailable(f"{t}: fundamentals fetch failed ({type(e).__name__}: {e})") from e


def stamp(df: pd.DataFrame) -> dict:
    """Provenance block attached to every data-derived payload."""
    last = df.index[-1]
    try:
        last = last.tz_convert(IST) if last.tzinfo else last
    except Exception:
        pass
    return {
        "last_bar_time": str(last),
        "fetched_at": now_ist(),
        "source": _provider.name,
        "data_quality": DATA_QUALITY if _provider.name == "yfinance" else "see provider",
    }
