"""
StockMerit - NSE RSI Screener
------------------------------
Daily screen of NSE stocks by 14-day RSI, with a per-stock detail view
covering market cap, support/resistance and a financial health scorecard.

Deploy free on Streamlit Community Cloud (share.streamlit.io). Locally:
  pip install -r requirements.txt
  streamlit run streamlit_app.py
"""

from __future__ import annotations

import datetime as dt
import io
import time
from collections import Counter
from urllib.parse import quote
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

RSI_PERIOD = 14
MCAP_LARGE_CR, MCAP_MID_CR, MCAP_SMALL_CR = 20000, 5000, 500

NIFTY50 = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS",
    "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
    "BEL.NS", "BHARTIARTL.NS", "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS",
    "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS",
    "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS", "ITC.NS", "JSWSTEEL.NS",
    "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS", "NESTLEIND.NS",
    "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS",
    "SBIN.NS", "SHRIRAMFIN.NS", "SUNPHARMA.NS", "TATACONSUM.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS",
    "TRENT.NS", "ULTRACEMCO.NS", "WIPRO.NS",
]

HEADERS = [
    "Date", "Stock Symbol", "Sector", "Current Price (Rs)", "Volume",
    "RSI", "PE", "Sec PE", "Buy/Sell", "52 Week High (Rs)", "1 Year Target (Rs)",
]

COL_LABELS = {
    "Date": "Date", "Stock Symbol": "Stock Symbol", "Sector": "Sector",
    "Current Price (Rs)": "Current Price", "Volume": "Volume", "RSI": "RSI",
    "PE": "PE", "Sec PE": "Sec PE",
    "Buy/Sell": "Buy / Sell", "52 Week High (Rs)": "52-Week High",
    "1 Year Target (Rs)": "1-Year Forecast",
}

SAMPLE_OI = [
    ("RELIANCE", 2960, 1.8, 12450000, 6.2),
    ("SBIN", 812, 2.1, 8990000, 9.5),
    ("ICICIBANK", 1248, 1.4, 7640000, 8.7),
    ("BAJFINANCE", 7240, 3.2, 2110000, 7.8),
    ("HDFCBANK", 1680, 0.9, 9820000, 4.1),
    ("TATAMOTORS", 985, -1.2, 6210000, 5.3),
    ("MARUTI", 12980, -1.5, 640000, 3.1),
    ("INFY", 1890, -0.6, 5540000, -3.4),
    ("TATASTEEL", 162, -0.8, 7120000, -2.7),
    ("AXISBANK", 1156, 0.4, 4380000, -4.9),
]

OI_SYMBOLS = [s for s, *_ in SAMPLE_OI]
DHAN_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"
DHAN_HIST_URL = "https://api.dhan.co/v2/charts/historical"
DHAN_SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


def _dhan_creds() -> tuple[str, str]:
    try:
        return (str(st.secrets.get("DHAN_CLIENT_ID", "")).strip(),
                str(st.secrets.get("DHAN_ACCESS_TOKEN", "")).strip())
    except Exception:
        return "", ""


def _sample_oi() -> list[dict]:
    return [{"sym": s, "ltp": l, "price_chg": pc, "oi": oi, "oi_chg": oc}
            for s, l, pc, oi, oc in SAMPLE_OI]


@st.cache_data(ttl=86400, show_spinner=False)
def _dhan_fut_map(symbols: tuple[str, ...]) -> dict:
    """Map each stock symbol -> nearest NSE stock-future security id (from scrip master)."""
    try:
        df = pd.read_csv(DHAN_SCRIP_URL, low_memory=False)
    except Exception:
        return {}
    up = {c.upper(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n in up:
                return up[n]
        return None

    c_exch = col("SEM_EXM_EXCH_ID", "EXCH_ID")
    c_instr = col("SEM_INSTRUMENT_NAME", "INSTRUMENT", "SEM_EXCH_INSTRUMENT_TYPE")
    c_sec = col("SEM_SMST_SECURITY_ID", "SECURITY_ID")
    c_exp = col("SEM_EXPIRY_DATE", "EXPIRY")
    c_und = col("SM_SYMBOL_NAME", "SEM_UNDERLYING_SYMBOL", "UNDERLYING_SYMBOL",
                "SEM_TRADING_SYMBOL")
    if not all([c_exch, c_instr, c_sec, c_und]):
        return {}
    try:
        d = df[(df[c_exch].astype(str) == "NSE")
               & (df[c_instr].astype(str).str.contains("FUTSTK", case=False, na=False))]
        out = {}
        for s in symbols:
            rows = d[d[c_und].astype(str).str.upper() == s]
            if rows.empty:
                continue
            if c_exp:
                rows = rows.sort_values(c_exp)
            out[s] = int(rows.iloc[0][c_sec])
        return out
    except Exception:
        return {}


@st.cache_data(ttl=300, show_spinner=False)
def get_oi_buildup() -> tuple[list[dict], bool]:
    """Return (rows, is_live). Uses Dhan when a token is configured, else sample data."""
    cid, tok = _dhan_creds()
    if not cid or not tok:
        return _sample_oi(), False
    try:
        secmap = _dhan_fut_map(tuple(OI_SYMBOLS))
        if not secmap:
            return _sample_oi(), False
        headers = {"access-token": tok, "client-id": cid,
                   "Content-Type": "application/json", "Accept": "application/json"}
        ids = list(secmap.values())
        q = requests.post(DHAN_QUOTE_URL, json={"NSE_FNO": ids},
                          headers=headers, timeout=8).json()
        qmap = (q.get("data") or {}).get("NSE_FNO") or {}
        today = dt.date.today()
        frm = (today - dt.timedelta(days=12)).isoformat()
        rows = []
        for sym, sid in secmap.items():
            qd = qmap.get(str(sid)) or {}
            ltp = qd.get("last_price", qd.get("ltp"))
            oi = qd.get("oi")
            hb = requests.post(DHAN_HIST_URL, headers=headers, timeout=8, json={
                "securityId": str(sid), "exchangeSegment": "NSE_FNO",
                "instrument": "FUTSTK", "expiryCode": 0,
                "fromDate": frm, "toDate": today.isoformat()}).json()
            closes = hb.get("close") or []
            ois = hb.get("open_interest") or hb.get("openInterest") or []
            if ltp is None and closes:
                ltp = closes[-1]
            if oi is None and ois:
                oi = ois[-1]
            prev_close = closes[-2] if len(closes) >= 2 else None
            prev_oi = ois[-2] if len(ois) >= 2 else None
            if None in (ltp, oi, prev_close, prev_oi) or not prev_close or not prev_oi:
                continue
            rows.append({"sym": sym, "ltp": float(ltp),
                         "price_chg": (float(ltp) / float(prev_close) - 1) * 100,
                         "oi": int(oi),
                         "oi_chg": (float(oi) / float(prev_oi) - 1) * 100})
        if not rows:
            return _sample_oi(), False
        rows.sort(key=lambda r: r["oi_chg"], reverse=True)
        return rows, True
    except Exception:
        return _sample_oi(), False


def oi_signal(price_chg: float, oi_chg: float) -> tuple[str, str]:
    if oi_chg >= 0:
        return ("Long Buildup", "#0B7A4B") if price_chg >= 0 else ("Short Buildup", "#B3261E")
    return ("Short Covering", "#0E7C86") if price_chg >= 0 else ("Long Unwinding", "#C77A0B")

UNIVERSES = ["All NIFTY Stocks", "NIFTY 50", "NIFTY 500"]

HEALTH_GROUPS = [
    ("Cash flow", ["Cash flow backs reported profit", "Operating cash flow positive",
                   "Free cash flow positive"]),
    ("Debt & solvency", ["Debt to equity", "Debt reduced vs last year",
                         "Interest coverage", "Current ratio"]),
    ("Efficiency", ["Receivables in line with sales", "Inventory in line with sales"]),
    ("Growth", ["Revenue growing (3y)", "Profit growing (3y)",
                "Profitable every year (3y)"]),
    ("Profitability", ["Return on equity", "Operating margin holding up",
                       "Net margin positive"]),
]

RECO_INFO = {
    "Strong Buy": "Highest-conviction analyst call — consensus strongly expects the "
                  "price to rise over the next 12 months.",
    "Buy": "Analysts expect the price to rise over the next 12 months.",
    "Hold": "Analysts expect roughly flat performance — wait and watch.",
    "Sell": "Analysts expect the price to fall — consider trimming or exiting.",
}

_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_INDEX500_LIST_URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"

SECTOR_PALETTE = ["#0E7C86", "#6B5BC7", "#C77A0B", "#1F6FB2",
                  "#0B7A4B", "#B3467A", "#2F8F5B", "#8A5A2B"]

DISCLAIMER = (
    "Everything shown here is for reference and general information only. It is not "
    "investment advice, not a recommendation to buy or sell any security, and not a "
    "solicitation of any kind. The Buy/Sell rating and 1-year target are third-party "
    "analyst consensus figures reproduced as-is, not the view of this app. Technical "
    "levels and health checks are mechanically computed from reported financials and "
    "may be wrong, incomplete or stale. Do your own research and consult a "
    "SEBI-registered adviser before investing. Securities investments carry market "
    "risk, including possible loss of capital."
)

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=Inter:wght@400;500&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root{
  --ink:#111C2B; --panel:#F5F8FA; --line:#DDE6ED;
  --teal:#0E7C86; --violet:#6B5BC7; --amber:#C77A0B; --blue:#1F6FB2;
  --green:#0B7A4B; --coral:#D8542F; --red:#B3261E; --plum:#B3467A;
  --text:#16202B; --muted:#5E6E7E;
}
html, body, [class*="css"], .stMarkdown { font-family:'Inter', sans-serif; color:var(--text); }
h1,h2,h3,h4,h5 { font-family:'Archivo', sans-serif; letter-spacing:-0.02em; }
[data-testid="stDataFrame"] { font-family:'IBM Plex Mono', monospace; }

.band{
  background:linear-gradient(100deg,#111C2B 0%,#123A44 55%,#0E7C86 100%);
  border-radius:16px; padding:24px 28px; margin-bottom:16px;
  display:flex; align-items:flex-end; justify-content:space-between;
  flex-wrap:wrap; gap:12px;
}
.band-name{ font-family:'Archivo',sans-serif; font-weight:700; font-size:1.7rem;
  color:#FFF; letter-spacing:-0.03em; line-height:1.1; display:flex; align-items:center; gap:10px; }
.band-name .brandmark{ width:34px; height:34px; border-radius:8px; background:#FFF;
  display:inline-flex; align-items:center; justify-content:center; }
.band-name .m{ color:#5CA8FF; }
.band-sub{ font-family:'IBM Plex Mono',monospace; font-size:0.78rem;
  color:#B6D8DC; margin-top:6px; }
.idxcard{ background:#FFF; border:1px solid #DDE6ED; border-radius:12px; padding:11px 14px; }
.idx-n{ font-size:0.66rem; text-transform:uppercase; letter-spacing:0.06em; color:#5E6E7E; font-weight:600; }
.idx-v{ font-family:'IBM Plex Mono',monospace; font-size:1.0rem; font-weight:600; margin-top:4px; }
.idx-c{ font-family:'IBM Plex Mono',monospace; font-size:0.74rem; font-weight:600; margin-top:2px; }
.band-date{ font-family:'IBM Plex Mono',monospace; font-size:0.78rem; color:#EAF6F7;
  background:rgba(255,255,255,0.14); border-radius:999px; padding:6px 14px; }

.sec-label{ font-size:0.72rem; text-transform:uppercase; letter-spacing:0.09em;
  color:var(--muted); font-weight:600; margin:2px 0 6px; }

.stat-row{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:4px; }
.stat{ flex:1 1 150px; border-radius:12px; padding:11px 13px; border:1px solid var(--line);
  border-top:4px solid var(--teal); background:#FFF; }
div[role="dialog"]{ max-width:600px !important; }
.stat-row .stat{ flex:1 1 120px; padding:9px 11px; }
.stat-row .stat-v{ font-size:1.0rem; }
.stat.v{ border-top-color:var(--violet); }
.stat.a{ border-top-color:var(--amber); }
.stat.b{ border-top-color:var(--blue); }
.stat.g{ border-top-color:var(--green); }
.stat-k{ font-size:0.68rem; text-transform:uppercase; letter-spacing:0.08em;
  color:var(--muted); font-weight:600; }
.stat-v{ font-family:'IBM Plex Mono',monospace; font-size:1.1rem; font-weight:600;
  margin-top:3px; line-height:1.2; }
.stat-n{ font-size:0.73rem; color:var(--muted); margin-top:3px; }

.score-row{ display:flex; gap:10px; flex-wrap:wrap; margin:6px 0 16px; }
.score{ flex:1 1 118px; border-radius:10px; padding:10px 13px; color:#FFF; }
.score.pos{ background:var(--green); }
.score.neu{ background:#6B7A8F; }
.score.neg{ background:var(--red); }
.score.nod{ background:#A8B4BF; }
.score-n{ font-family:'IBM Plex Mono',monospace; font-size:1.4rem; font-weight:600;
  line-height:1; }
.score-l{ font-size:0.75rem; margin-top:5px; opacity:0.92; }

.chk{ display:flex; align-items:baseline; gap:10px; padding:8px 0;
  border-bottom:1px solid var(--line); font-size:0.87rem; }
.chk:last-child{ border-bottom:none; }
.tag{ font-family:'IBM Plex Mono',monospace; font-size:0.65rem; font-weight:600;
  padding:3px 8px; border-radius:5px; text-transform:uppercase; letter-spacing:0.05em;
  flex:0 0 auto; min-width:64px; text-align:center; color:#FFF; }
.tag.Yes{ background:var(--green); }
.tag.No{ background:var(--red); }
.tag.Neutral{ background:#6B7A8F; }
.tag.NoData{ background:#BFC9D2; }
.chk-n{ flex:1 1 auto; }
.chk-d{ font-family:'IBM Plex Mono',monospace; font-size:0.78rem; color:var(--muted); }

.lvl{ display:flex; justify-content:space-between; padding:7px 11px; border-radius:7px;
  font-family:'IBM Plex Mono',monospace; font-size:0.85rem; margin-bottom:4px;
  color:#FFF; }
.lvl.r{ background:#D8542F; }
.lvl.r2{ background:#E07E5F; }
.lvl.p{ background:var(--violet); font-weight:600; }
.lvl.s{ background:#2F8F5B; }
.lvl.s2{ background:#5CAE82; }
.lvl.now{ background:var(--ink); font-weight:600; }

.disc{ font-size:0.76rem; color:var(--muted); line-height:1.6;
  border-top:1px solid var(--line); padding-top:14px; margin-top:10px; }
.stButton>button{ border-radius:9px; font-weight:500; }

/* --- custom results table (matches the prototype) --- */
.sh-tablewrap{ border:1px solid #E5ECF1; border-radius:12px; overflow-x:auto; margin-top:6px; }
.sh-table{ width:100%; border-collapse:collapse; }
.sh-table thead th{ background:#F5F8FA; padding:11px 14px; font-size:0.68rem;
  text-transform:uppercase; letter-spacing:0.07em; color:var(--muted); font-weight:600;
  white-space:nowrap; }
.sh-table .th-r{ text-align:right; } .sh-table .th-c{ text-align:center; }
.sh-table .th-l{ text-align:left; }
.sh-table td{ padding:12px 14px; border-top:1px solid #EEF3F6; white-space:nowrap;
  font-size:0.85rem; color:var(--text); }
.sh-table tbody tr{ transition:background .1s; }
.sh-table tbody tr:hover{ background:#F5F8FA; }
.sh-table .c-date{ font-family:'IBM Plex Mono',monospace; font-size:0.8rem; color:var(--muted); }
.sh-table a.c-sym{ font-family:'IBM Plex Mono',monospace; font-weight:600; font-size:0.86rem;
  color:var(--teal); text-decoration:none; }
.sh-table a.c-sym:hover{ text-decoration:underline; }
.sh-table .c-price{ text-decoration:underline dotted; text-underline-offset:3px;
  text-decoration-color:#9BB4C4; cursor:help; }
.sh-table .c-sec{ font-weight:500; }
.sh-table .c-num{ font-family:'IBM Plex Mono',monospace; text-align:right; }
.sh-table .c-muted{ color:var(--muted); }
.sh-table .c-rsi{ font-weight:600; font-size:0.9rem; }
.sh-table .c-reco{ text-align:center; }
.sh-table .pill{ font-family:'IBM Plex Mono',monospace; font-size:0.68rem; font-weight:600;
  padding:3px 9px; border-radius:6px; color:#FFF; white-space:nowrap; }
.sh-hint{ margin-top:10px; font-size:0.75rem; color:#8794A1; }
</style>
"""


# ------------------------------ indicators --------------------------------

def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> float | None:
    close = close.dropna()
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    g, l = avg_gain.iloc[-1], avg_loss.iloc[-1]
    if pd.isna(g) or pd.isna(l):
        return None
    if l == 0:
        return 100.0
    return float(100.0 - (100.0 / (1.0 + (g / l))))


def classify_market_cap(mcap_inr: float | None) -> str:
    if not mcap_inr or mcap_inr <= 0:
        return "n/a"
    cr = mcap_inr / 1e7
    if cr >= MCAP_LARGE_CR:
        return "Large cap"
    if cr >= MCAP_MID_CR:
        return "Mid cap"
    if cr >= MCAP_SMALL_CR:
        return "Small cap"
    return "Micro cap"


def pivot_levels(h: float, l: float, c: float) -> dict:
    p = (h + l + c) / 3.0
    return {"R3": h + 2 * (p - l), "R2": p + (h - l), "R1": 2 * p - l, "Pivot": p,
            "S1": 2 * p - h, "S2": p - (h - l), "S3": l - 2 * (h - p)}


def swing_levels(close: pd.Series, price: float, window: int = 10):
    s = close.dropna()
    highs = s[(s.shift(window) < s) & (s.shift(-window) < s)]
    lows = s[(s.shift(window) > s) & (s.shift(-window) > s)]
    sup, res = lows[lows < price], highs[highs > price]
    return (float(sup.max()) if len(sup) else None,
            float(res.min()) if len(res) else None)


def sector_color(name) -> str:
    if not isinstance(name, str) or name in ("n/a", "not loaded", ""):
        return "#8794A1"
    return SECTOR_PALETTE[sum(ord(c) for c in name) % len(SECTOR_PALETTE)]


def rsi_color(v) -> str:
    if not isinstance(v, (int, float)):
        return "#5E6E7E"
    if v >= 80:
        return "#B3261E"
    if v >= 70:
        return "#D8542F"
    return "#C77A0B"


def reco_color(v) -> str:
    s = str(v).lower()
    if "strong buy" in s or s == "buy":
        return "#0B7A4B"
    if "sell" in s:
        return "#B3261E"
    if "hold" in s:
        return "#C77A0B"
    return "#8794A1"


def reco_info(v) -> str:
    return RECO_INFO.get(str(v), "")


# --------------------------- health scorecard -----------------------------

def _find_row(fin, *keys):
    if fin is None or getattr(fin, "empty", True):
        return None
    for k in keys:
        for idx in fin.index:
            if k.lower() in str(idx).lower():
                return fin.loc[idx]
    return None


def _vals(series, n=1):
    if series is None:
        return None
    s = pd.Series(series).dropna()
    if s.empty:
        return None
    return s.sort_index(ascending=False).head(n).astype(float)


def _band(v, good, ok, higher_better=True):
    if v is None:
        return "No Data"
    if higher_better:
        return "Yes" if v >= good else ("Neutral" if v >= ok else "No")
    return "Yes" if v <= good else ("Neutral" if v <= ok else "No")


def health_checks(income, balance, cash) -> list[dict]:
    out = []

    def add(name, verdict, detail=""):
        out.append({"name": name, "verdict": verdict, "detail": detail})

    ni = _find_row(income, "Net Income")
    cfo = _find_row(cash, "Operating Cash Flow", "Total Cash From Operating")
    n3, c3 = _vals(ni, 3), _vals(cfo, 3)
    if n3 is not None and c3 is not None and n3.sum() != 0:
        r = c3.sum() / n3.sum()
        add("Cash flow backs reported profit", _band(r, 0.8, 0.5), f"{r:.2f}x")
    else:
        add("Cash flow backs reported profit", "No Data")

    c1 = _vals(cfo, 1)
    add("Operating cash flow positive",
        "Yes" if c1 is not None and c1.iloc[0] > 0 else ("No" if c1 is not None else "No Data"),
        f"Rs {c1.iloc[0]/1e7:,.0f} Cr" if c1 is not None else "")

    cx = _vals(_find_row(cash, "Capital Expenditure"), 1)
    if c1 is not None and cx is not None:
        fcf = c1.iloc[0] - abs(cx.iloc[0])
        add("Free cash flow positive", "Yes" if fcf > 0 else "No", f"Rs {fcf/1e7:,.0f} Cr")
    else:
        add("Free cash flow positive", "No Data")

    td = _vals(_find_row(balance, "Total Debt"), 2)
    eq = _vals(_find_row(balance, "Stockholders Equity", "Total Equity"), 1)
    if td is not None and eq is not None and eq.iloc[0]:
        de = td.iloc[0] / eq.iloc[0]
        add("Debt to equity", _band(de, 0.5, 1.5, False), f"{de:.2f}x")
    else:
        add("Debt to equity", "No Data")

    if td is not None and len(td) == 2:
        add("Debt reduced vs last year", "Yes" if td.iloc[0] < td.iloc[1] else "No",
            f"{td.iloc[0]/1e7:,.0f} Cr")
    else:
        add("Debt reduced vs last year", "No Data")

    ebit = _vals(_find_row(income, "EBIT", "Operating Income"), 1)
    ie = _vals(_find_row(income, "Interest Expense"), 1)
    if ebit is not None and ie is not None and ie.iloc[0]:
        cov = ebit.iloc[0] / abs(ie.iloc[0])
        add("Interest coverage", _band(cov, 5, 2), f"{cov:.1f}x")
    else:
        add("Interest coverage", "No Data")

    ca = _vals(_find_row(balance, "Current Assets"), 1)
    cl = _vals(_find_row(balance, "Current Liabilities"), 1)
    if ca is not None and cl is not None and cl.iloc[0]:
        cr = ca.iloc[0] / cl.iloc[0]
        add("Current ratio", _band(cr, 1.5, 1.0), f"{cr:.2f}x")
    else:
        add("Current ratio", "No Data")

    rev = _vals(_find_row(income, "Total Revenue"), 3)
    rg = (rev.iloc[0] / rev.iloc[1] - 1) if rev is not None and len(rev) >= 2 and rev.iloc[1] else None
    for label, ser in (("Receivables in line with sales",
                        _vals(_find_row(balance, "Receivables", "Accounts Receivable"), 2)),
                       ("Inventory in line with sales",
                        _vals(_find_row(balance, "Inventory"), 2))):
        if ser is not None and len(ser) == 2 and ser.iloc[1] and rg is not None:
            g = ser.iloc[0] / ser.iloc[1] - 1
            add(label, _band(g - rg, 0.10, 0.25, False), f"{g*100:.0f}% vs {rg*100:.0f}%")
        else:
            add(label, "No Data")

    if rev is not None and len(rev) >= 3 and rev.iloc[2]:
        add("Revenue growing (3y)", "Yes" if rev.iloc[0] > rev.iloc[2] else "No",
            f"{(rev.iloc[0]/rev.iloc[2]-1)*100:.0f}%")
    else:
        add("Revenue growing (3y)", "No Data")

    if n3 is not None and len(n3) >= 3 and n3.iloc[2]:
        add("Profit growing (3y)", "Yes" if n3.iloc[0] > n3.iloc[2] else "No",
            f"{(n3.iloc[0]/n3.iloc[2]-1)*100:.0f}%")
        add("Profitable every year (3y)", "Yes" if (n3 > 0).all() else "No")
    else:
        add("Profit growing (3y)", "No Data")
        add("Profitable every year (3y)", "No Data")

    if n3 is not None and eq is not None and eq.iloc[0]:
        roe = n3.iloc[0] / eq.iloc[0] * 100
        add("Return on equity", _band(roe, 15, 8), f"{roe:.1f}%")
    else:
        add("Return on equity", "No Data")

    om = _vals(_find_row(income, "Operating Income", "EBIT"), 2)
    if (om is not None and rev is not None and len(om) == 2 and len(rev) >= 2
            and rev.iloc[0] and rev.iloc[1]):
        m0, m1 = om.iloc[0] / rev.iloc[0], om.iloc[1] / rev.iloc[1]
        add("Operating margin holding up", "Yes" if m0 >= m1 else "No",
            f"{m0*100:.1f}% vs {m1*100:.1f}%")
    else:
        add("Operating margin holding up", "No Data")

    if n3 is not None and rev is not None and rev.iloc[0]:
        nm = n3.iloc[0] / rev.iloc[0] * 100
        add("Net margin positive", "Yes" if nm > 0 else "No", f"{nm:.1f}%")
    else:
        add("Net margin positive", "No Data")

    return out


# ------------------------------- universe ---------------------------------

def _fetch_symbol_csv(url: str) -> list[str]:
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/csv,*/*"}
    try:
        r = requests.get(url, headers=hdr, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        col = next(c for c in df.columns if c.strip().upper() == "SYMBOL")
        return [f"{s.strip()}.NS" for s in df[col].dropna().astype(str) if s.strip()]
    except Exception:
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def load_all_nse_tickers() -> list[str]:
    return _fetch_symbol_csv(_EQUITY_LIST_URL)


@st.cache_data(ttl=86400, show_spinner=False)
def load_nifty500_tickers() -> list[str]:
    return _fetch_symbol_csv(_INDEX500_LIST_URL)


@st.cache_data(ttl=86400, show_spinner=False)
def load_face_values() -> dict:
    """Map SYMBOL -> face value from NSE's public EQUITY_L.csv (no auth needed)."""
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/csv,*/*"}
    try:
        r = requests.get(_EQUITY_LIST_URL, headers=hdr, timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip().upper() for c in df.columns]
        sym_c = next((c for c in df.columns if c == "SYMBOL"), None)
        fv_c = next((c for c in df.columns if c == "FACE VALUE"), None)
        if not sym_c or not fv_c:
            return {}
        out = {}
        for _, row in df[[sym_c, fv_c]].dropna().iterrows():
            try:
                out[str(row[sym_c]).strip().upper()] = float(row[fv_c])
            except Exception:
                continue
        return out
    except Exception:
        return {}


def parse_uploaded_symbols(text: str) -> list[str]:
    out = []
    for line in text.replace(",", "\n").splitlines():
        s = line.strip().upper()
        if not s or s == "SYMBOL":
            continue
        out.append(s if s.endswith(".NS") else f"{s}.NS")
    return out


def tickers_for(universe_choice: str, uploaded: str | None = None) -> list[str]:
    if universe_choice == "NIFTY 50":
        return list(NIFTY50)
    if universe_choice == "NIFTY 500":
        return load_nifty500_tickers()
    if universe_choice == "All NIFTY Stocks":
        return load_all_nse_tickers()
    return []


SCAN_SEP = "|"


def _cfg_str(u: str, th, n) -> str:
    return f"{u}{SCAN_SEP}{th}{SCAN_SEP}{n}"


# ----------------------------- data fetching ------------------------------

def fetch_fundamentals(symbol: str) -> dict:
    name = symbol.replace(".NS", "")
    sector, reco, target, pe = "n/a", "n/a", "n/a", None
    tk = yf.Ticker(symbol)
    try:
        info = tk.info or {}
        name = info.get("shortName") or info.get("longName") or name
        if info.get("sector"):
            sector = str(info["sector"]).strip()
        rk = info.get("recommendationKey")
        if rk and rk.lower() != "none":
            reco = rk.replace("_", " ").title()
        tp = info.get("targetMeanPrice")
        if tp:
            target = round(float(tp), 2)
        pev = info.get("trailingPE")
        if pev:
            pe = round(float(pev), 2)
    except Exception:
        pass
    if target == "n/a":
        try:
            m = (tk.analyst_price_targets or {}).get("mean")
            if m:
                target = round(float(m), 2)
        except Exception:
            pass
    return {"name": name, "sector": sector, "reco": reco, "target": target, "pe": pe}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_detail(symbol: str) -> dict:
    out = {"name": symbol.replace(".NS", ""), "sector": "n/a", "mcap": None,
           "price": None, "high52": None, "low52": None, "rsi": None,
           "reco": "n/a", "target": "n/a", "levels": {}, "swing": (None, None),
           "checks": [], "face": None, "book": None, "beta": None, "error": None}
    try:
        tk = yf.Ticker(symbol)
        hist = tk.history(period="2y", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            out["error"] = "No price history found for this symbol. Check the spelling."
            return out
        close = hist["Close"].dropna()
        out["price"] = float(close.iloc[-1])
        out["rsi"] = compute_rsi(close)
        yr = hist.tail(252)
        out["high52"], out["low52"] = float(yr["High"].max()), float(yr["Low"].min())

        monthly = hist.resample("ME").agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(monthly) >= 2:
            prev = monthly.iloc[-2]
            out["levels"] = pivot_levels(float(prev["High"]), float(prev["Low"]),
                                         float(prev["Close"]))
        out["swing"] = swing_levels(close.tail(180), out["price"])

        try:
            info = tk.info or {}
            out["name"] = info.get("shortName") or info.get("longName") or out["name"]
            out["mcap"] = info.get("marketCap")
            if info.get("sector"):
                out["sector"] = str(info["sector"]).strip()
            rk = info.get("recommendationKey")
            if rk and rk.lower() != "none":
                out["reco"] = rk.replace("_", " ").title()
            tp = info.get("targetMeanPrice")
            if tp:
                out["target"] = round(float(tp), 2)
            out["book"] = info.get("bookValue")
            out["beta"] = info.get("beta")
            out["face"] = info.get("faceValue") or info.get("parValue")
            if out["face"] is None:
                out["face"] = load_face_values().get(symbol.replace(".NS", "").upper())
        except Exception:
            pass
        try:
            out["checks"] = health_checks(tk.income_stmt, tk.balance_sheet, tk.cashflow)
        except Exception:
            out["checks"] = []
    except Exception as exc:
        out["error"] = f"Could not load this stock ({type(exc).__name__}). Try again shortly."
    return out


@st.cache_data(ttl=900, show_spinner=False)
def fetch_news(limit: int = 8) -> list[dict]:
    """Latest market headlines from public RSS feeds (headline + link only)."""
    feeds = [
        ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
        ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
        ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ]
    out = []
    for source, url in feeds:
        try:
            r = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if title and link:
                    out.append({"title": title, "url": link, "source": source})
        except Exception:
            continue
    return out[:limit]


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def market_open() -> bool:
    now = dt.datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return dt.time(9, 15) <= now.time() <= dt.time(15, 30)


@st.cache_data(ttl=20, show_spinner=False)
def fetch_indices(bucket: int = 0) -> list[dict]:
    specs = [("NIFTY 50", "^NSEI"), ("SENSEX", "^BSESN"),
             ("BANK NIFTY", "^NSEBANK"), ("NIFTY IT", "^CNXIT"),
             ("NIFTY MIDCAP 100", "^NSEMDCP50")]
    out = []
    for name, tk in specs:
        try:
            h = yf.Ticker(tk).history(period="5d")["Close"].dropna()
            if len(h) >= 2:
                val = float(h.iloc[-1])
                chg = (val / float(h.iloc[-2]) - 1) * 100
                out.append({"name": name, "value": val, "chg": chg})
        except Exception:
            continue
    return out


@st.cache_data(ttl=300, show_spinner=False)
def scan(tickers: tuple[str, ...], threshold: float, fetch_fund_limit: int) -> pd.DataFrame:
    bar = st.progress(0.0, text="Downloading price history...")
    close_map, high_map, vol_map = {}, {}, {}
    batch, n = 200, len(tickers)
    for i in range(0, n, batch):
        chunk = list(tickers[i:i + batch])
        data = yf.download(chunk, period="1y", interval="1d", group_by="ticker",
                           auto_adjust=False, threads=True, progress=False)
        for sym in chunk:
            try:
                df = data[sym] if len(chunk) > 1 else data
                c = df["Close"].dropna()
                if c.empty:
                    continue
                close_map[sym] = c
                high_map[sym] = float(df["High"].dropna().max())
                v = df["Volume"].dropna() if "Volume" in df else None
                vol_map[sym] = float(v.iloc[-1]) if v is not None and not v.empty else None
            except Exception:
                continue
        bar.progress(min((i + batch) / n, 1.0), text=f"Downloaded {min(i+batch, n)}/{n} stocks")

    hits = []
    for sym, c in close_map.items():
        rsi = compute_rsi(c)
        if rsi is not None and rsi >= threshold:
            hits.append((sym, rsi, float(c.iloc[-1]), high_map.get(sym)))
    hits.sort(key=lambda x: x[1], reverse=True)

    def _sma(series, n):
        if series is None or len(series) < n:
            return None
        return round(float(series.rolling(n).mean().iloc[-1]), 2)

    rows, total = [], min(len(hits), fetch_fund_limit)
    bar.progress(0.0, text=f"Loading details for {total} matches")
    for idx, (sym, rsi, price, high52) in enumerate(hits):
        if idx < fetch_fund_limit:
            f = fetch_fundamentals(sym)
            time.sleep(0.4)
            bar.progress((idx + 1) / max(total, 1), text=f"Loaded {idx+1}/{total}")
        else:
            f = {"name": sym.replace(".NS", ""), "sector": "not loaded",
                 "reco": "not loaded", "target": "not loaded", "pe": None}
        cser = close_map.get(sym)
        rows.append({
            "Date": dt.date.today().strftime("%d-%m-%Y"),
            "Stock Symbol": sym.replace(".NS", ""),
            "Sector": f["sector"],
            "Current Price (Rs)": round(price, 2),
            "Volume": vol_map.get(sym),
            "52 Week High (Rs)": round(high52, 2) if high52 else None,
            "RSI": round(rsi, 1),
            "PE": f.get("pe"),
            "Sec PE": None,
            "Buy/Sell": f["reco"],
            "1 Year Target (Rs)": f["target"],
            "_SMA20": _sma(cser, 20),
            "_SMA50": _sma(cser, 50),
            "_SMA200": _sma(cser, 200),
        })
    # Sector P/E = mean trailing P/E of scanned stocks in the same sector
    sec_pes: dict[str, list[float]] = {}
    for r in rows:
        if isinstance(r["PE"], (int, float)) and isinstance(r["Sector"], str):
            sec_pes.setdefault(r["Sector"], []).append(float(r["PE"]))
    sec_avg = {k: round(sum(v) / len(v), 2) for k, v in sec_pes.items()}
    for r in rows:
        r["Sec PE"] = sec_avg.get(r["Sector"])
    bar.empty()
    return pd.DataFrame(rows, columns=HEADERS + ["_SMA20", "_SMA50", "_SMA200"])


# --------------------------------- excel ----------------------------------

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "RSI Screener"
    hfill = PatternFill("solid", fgColor="111C2B")
    hfont = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    bfont = Font(name="Arial", size=11)
    hot = PatternFill("solid", fgColor="FDEBE4")

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.fill, cell.font = hfill, hfont
        cell.alignment = Alignment(horizontal="center", vertical="center")

    col_of = {h: i + 1 for i, h in enumerate(HEADERS)}
    for _, r in df.iterrows():
        ws.append([r[h] for h in HEADERS])
        i = ws.max_row
        for cell in ws[i]:
            cell.font = bfont
        for hh in ("Current Price (Rs)", "52 Week High (Rs)", "1 Year Target (Rs)",
                   "PE", "Sec PE"):
            c = ws.cell(row=i, column=col_of[hh])
            if isinstance(c.value, (int, float)):
                c.number_format = "#,##0.00"
        vcell = ws.cell(row=i, column=col_of["Volume"])
        if isinstance(vcell.value, (int, float)):
            vcell.number_format = "#,##0"
        ws.cell(row=i, column=col_of["RSI"]).number_format = "0.0"
        if isinstance(r["RSI"], (int, float)) and r["RSI"] >= 70:
            for cell in ws[i]:
                cell.fill = hot

    for i, w in enumerate([12, 16, 22, 16, 14, 8, 8, 10, 14, 16, 16], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    last_col = get_column_letter(len(HEADERS))
    ws.auto_filter.ref = f"A1:{last_col}{max(ws.max_row, 2)}"
    ws.cell(row=ws.max_row + 2, column=1, value="Disclaimer: " + DISCLAIMER).font = \
        Font(name="Arial", italic=True, size=9)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------ detail view -------------------------------

def _stat(label: str, value: str, note: str = "", tone: str = "") -> str:
    n = f'<div class="stat-n">{note}</div>' if note else ""
    return (f'<div class="stat {tone}"><div class="stat-k">{label}</div>'
            f'<div class="stat-v">{value}</div>{n}</div>')


def render_detail(symbol: str):
    d = fetch_detail(f"{symbol}.NS")
    if d["error"]:
        st.warning(d["error"])
        return

    st.markdown(f"### {symbol}")
    st.markdown(
        f'<span style="color:{sector_color(d["sector"])};font-weight:600">'
        f'{d["sector"]}</span> · {d["name"]}', unsafe_allow_html=True)
    st.write("")

    price = f"Rs {d['price']:,.2f}" if d["price"] else "n/a"
    mcap = f"Rs {d['mcap']/1e7:,.0f} Cr" if d["mcap"] else "n/a"
    rsi = f"{d['rsi']:.1f}" if d["rsi"] else "n/a"
    rng = f"{d['low52']:,.0f} – {d['high52']:,.0f}" if d["high52"] else "n/a"
    tgt = f"Rs {d['target']:,.2f}" if isinstance(d["target"], (int, float)) else "n/a"
    face = f"Rs {d['face']:,.2f}" if isinstance(d["face"], (int, float)) else "n/a"
    book = f"Rs {d['book']:,.2f}" if isinstance(d["book"], (int, float)) else "n/a"
    beta = f"{d['beta']:.2f}" if isinstance(d["beta"], (int, float)) else "n/a"

    cards = "".join([
        _stat("Price", price, "", ""),
        _stat("Market cap", mcap, classify_market_cap(d["mcap"]), "v"),
        _stat("RSI (14d)", rsi,
              "overbought zone" if d["rsi"] and d["rsi"] >= 70 else "momentum", "a"),
        _stat("52-week range", rng, "", "b"),
        _stat("1-year forecast", tgt, f"consensus: {d['reco']}", "g"),
        _stat("Face value", face, "", "v"),
        _stat("Book value", book, "per share", "b"),
        _stat("Beta", beta, "volatility vs market", "a"),
    ])
    st.markdown(f'<div class="stat-row">{cards}</div>', unsafe_allow_html=True)
    st.caption(f"Live price as of {dt.datetime.now().strftime('%d-%m-%Y, %H:%M')} "
               "(server time).")

    st.write("")
    tab_levels, tab_health = st.tabs(["Price levels", "Financial health"])

    with tab_levels:
        left, right = st.columns(2)
        with left:
            st.markdown('<div class="sec-label">Monthly pivot levels</div>',
                        unsafe_allow_html=True)
            if d["levels"]:
                lv, p = d["levels"], d["price"]
                tone = {"R3": "r", "R2": "r2", "R1": "r2", "Pivot": "p",
                        "S1": "s2", "S2": "s2", "S3": "s"}
                html, placed = [], False
                for k in ["R3", "R2", "R1", "Pivot", "S1", "S2", "S3"]:
                    if not placed and p and lv[k] < p:
                        html.append(f'<div class="lvl now"><span>Current price</span>'
                                    f'<span>{p:,.2f}</span></div>')
                        placed = True
                    html.append(f'<div class="lvl {tone[k]}"><span>{k}</span>'
                                f'<span>{lv[k]:,.2f}</span></div>')
                if not placed and p:
                    html.append(f'<div class="lvl now"><span>Current price</span>'
                                f'<span>{p:,.2f}</span></div>')
                st.markdown("".join(html), unsafe_allow_html=True)
                st.caption("Floor-trader pivots from the last completed month.")
            else:
                st.write("Not enough history to compute pivots.")
        with right:
            st.markdown('<div class="sec-label">Nearest swing levels</div>',
                        unsafe_allow_html=True)
            sup, res = d["swing"]
            st.markdown(
                f'<div class="lvl r"><span>Resistance</span><span>'
                f'{f"{res:,.2f}" if res else "none in range"}</span></div>'
                f'<div class="lvl now"><span>Current price</span><span>'
                f'{d["price"]:,.2f}</span></div>'
                f'<div class="lvl s"><span>Support</span><span>'
                f'{f"{sup:,.2f}" if sup else "none in range"}</span></div>',
                unsafe_allow_html=True)
            st.caption("Local highs and lows over roughly the last six months.")

    with tab_health:
        checks = d["checks"]
        if not checks:
            st.write("No financial statements available for this stock.")
        else:
            cnt = Counter(c["verdict"] for c in checks)
            st.markdown(
                '<div class="score-row">'
                f'<div class="score pos"><div class="score-n">{cnt.get("Yes",0)}</div>'
                '<div class="score-l">Positive</div></div>'
                f'<div class="score neu"><div class="score-n">{cnt.get("Neutral",0)}</div>'
                '<div class="score-l">Neutral</div></div>'
                f'<div class="score neg"><div class="score-n">{cnt.get("No",0)}</div>'
                '<div class="score-l">Negative</div></div>'
                f'<div class="score nod"><div class="score-n">{cnt.get("No Data",0)}</div>'
                '<div class="score-l">No data</div></div>'
                '</div>', unsafe_allow_html=True)
            st.caption("Grouped by theme — expand a category to see its checks.")
            by_name = {c["name"]: c for c in checks}
            for cat, names in HEALTH_GROUPS:
                gc = [by_name[n] for n in names if n in by_name]
                if not gc:
                    continue
                pos = sum(1 for c in gc if c["verdict"] == "Yes")
                with st.expander(f"{cat}  ·  {pos}/{len(gc)} positive", expanded=False):
                    st.markdown("".join(
                        f'<div class="chk"><span class="tag {c["verdict"].replace(" ","")}">'
                        f'{c["verdict"]}</span><span class="chk-n">{c["name"]}</span>'
                        f'<span class="chk-d">{c["detail"]}</span></div>' for c in gc),
                        unsafe_allow_html=True)
            st.caption("Mechanical checks on reported annual financials. A negative flag "
                       "marks something worth investigating, not a verdict on the company. "
                       "Ratios like debt-to-equity do not carry the same meaning for banks "
                       "and NBFCs. Not covered: promoter pledging, auditor changes, "
                       "related-party transactions, contingent liabilities.")


@st.dialog("Stock detail", width="small")
def detail_dialog(symbol: str):
    render_detail(symbol)


# ---------------------------------- app -----------------------------------

st.set_page_config(page_title="StockMerit — NSE RSI Screener",
                   page_icon="📊", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

st.markdown(
    '<div class="band"><div><div class="band-name">'
    '<span class="brandmark"><svg width="22" height="22" viewBox="0 0 32 32" fill="none">'
    '<defs><linearGradient id="smg" x1="2" y1="26" x2="30" y2="6" gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#1B4DB8"/><stop offset="1" stop-color="#3E9BFF"/></linearGradient></defs>'
    '<path d="M4 22 L13 15 L18 19 L27 8" stroke="url(#smg)" stroke-width="3.4" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M20 8 H28 V16" stroke="url(#smg)" stroke-width="3.4" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg></span>'
    '<span style="color:#5CA8FF">Stock</span>'
    '<span style="color:#FFFFFF">Mer</span>'
    '<span style="color:#5CA8FF">it</span></div>'
    '<div class="band-sub">Analyze at one place …</div></div>'
    f'<div class="band-date">{dt.date.today().strftime("%d-%m-%Y")}</div></div>',
    unsafe_allow_html=True)

# --- sidebar: search ---
with st.sidebar:
    all_syms = load_all_nse_tickers()
    if all_syms:
        opts = [s.replace(".NS", "") for s in all_syms]
        picked_search = st.selectbox(" ", opts, index=None, placeholder="Search Stock",
                                     label_visibility="collapsed")
        if picked_search and st.button("Open detail", use_container_width=True,
                                       type="primary"):
            detail_dialog(picked_search)
    else:
        typed = st.text_input(" ", placeholder="Search Stock", label_visibility="collapsed")
        if st.button("Open detail", use_container_width=True, type="primary") \
                and typed.strip():
            detail_dialog(typed.strip().upper())

with st.sidebar:
    st.markdown("---")
    st.toggle("Live updates", value=True, key="live_on")
    if st.session_state.get("live_on"):
        st.select_slider("Refresh every (sec)", options=[1, 5, 10, 15, 30, 60],
                         value=1, key="live_every")
live_on = st.session_state.get("live_on", True)
live_every = st.session_state.get("live_every", 1)

# --- top nav: Screener / News / Stock OI ---
st.session_state.setdefault("view", "Screener")
view = st.radio("view", ["Screener", "News", "Stock OI"],
                horizontal=True, label_visibility="collapsed", key="view")

if view == "News":
    st.markdown("### Financial news")
    _news = fetch_news(14)
    if _news:
        for _n in _news:
            st.markdown(
                f'<a href="{_n["url"]}" target="_blank" style="font-size:0.95rem; '
                f'font-weight:600; line-height:1.35; display:block;">{_n["title"]}</a>'
                f'<div style="font-size:0.74rem; color:#0E7C86; margin:2px 0 12px;">'
                f'{_n["source"]}</div>', unsafe_allow_html=True)
        st.caption("Headlines from public RSS feeds — click to read at the source.")
    else:
        st.info("News feed unavailable right now.")
    st.stop()

if view == "Stock OI":
    _oi_rows_data, _oi_live = get_oi_buildup()
    st.markdown("### Open interest — buildup")
    st.caption(("Live via Dhan API. " if _oi_live else "Sample data — add your Dhan "
                "token in app secrets for live values. ")
               + "Rise in OI with rise in price = long buildup.")
    _oi_head = "".join(f'<th class="th-{a}">{h}</th>' for h, a in [
        ("Stock Symbol", "l"), ("LTP", "r"), ("Rise In Price", "r"),
        ("Open Interest", "r"), ("Rise In OI", "r"), ("Signal", "c")])
    _oi_rows = []
    for _r in _oi_rows_data:
        _pc, _oc = _r["price_chg"], _r["oi_chg"]
        _sig, _sc = oi_signal(_pc, _oc)
        _pcl = "#0B7A4B" if _pc >= 0 else "#B3261E"
        _ocl = "#0B7A4B" if _oc >= 0 else "#B3261E"
        _oi_rows.append(
            "<tr>"
            f'<td><a class="c-sym">{_r["sym"]}</a></td>'
            f'<td class="c-num">{_r["ltp"]:,.2f}</td>'
            f'<td class="c-num" style="color:{_pcl};font-weight:600">{_pc:+.2f}%</td>'
            f'<td class="c-num c-muted">{int(_r["oi"]):,}</td>'
            f'<td class="c-num" style="color:{_ocl};font-weight:600">{_oc:+.2f}%</td>'
            f'<td class="c-reco"><span class="pill" style="background:{_sc}">{_sig}</span></td>'
            "</tr>")
    st.markdown(
        '<div class="sh-tablewrap"><table class="sh-table"><thead><tr>'
        + _oi_head + "</tr></thead><tbody>" + "".join(_oi_rows)
        + "</tbody></table></div>", unsafe_allow_html=True)
    st.stop()

# --- Screener view ---
_live_active = live_on and market_open()

@st.fragment(run_every=(live_every if _live_active else None))
def render_indices_strip():
    bucket = int(time.time() // max(int(live_every), 5))
    _idx = fetch_indices(bucket)
    if _idx:
        for _c, _ix in zip(st.columns(len(_idx)), _idx):
            _clr = "#0B7A4B" if _ix["chg"] >= 0 else "#B3261E"
            _arr = "▲ +" if _ix["chg"] >= 0 else "▼ "
            _c.markdown(
                f'<div class="idxcard"><div class="idx-n">{_ix["name"]}</div>'
                f'<div class="idx-v">{_ix["value"]:,.2f}</div>'
                f'<div class="idx-c" style="color:{_clr}">{_arr}{abs(_ix["chg"]):.2f}%</div></div>',
                unsafe_allow_html=True)
    _ts = dt.datetime.now(IST).strftime("%H:%M:%S")
    if _live_active:
        st.caption(f"🟢 Live · updated {_ts} IST · refreshing every {int(live_every)}s")
    elif live_on:
        st.caption(f"⚪ Market closed · showing last close ({_ts} IST)")
    else:
        st.caption(f"⏸ Live updates paused · {_ts} IST")

render_indices_strip()

# --- hyperlink handler: ?stock=SYMBOL opens the dialog ---
qp_stock = st.query_params.get("stock")
if qp_stock and st.session_state.get("qp_opened") != qp_stock:
    st.session_state["qp_opened"] = qp_stock
    detail_dialog(str(qp_stock).upper())

# --- universe as buttons ---
st.markdown('<div class="sec-label">Stocks to scan</div>', unsafe_allow_html=True)
if "universe" not in st.session_state:
    st.session_state["universe"] = "NIFTY 50"
ucols = st.columns(len(UNIVERSES))
for col, name in zip(ucols, UNIVERSES):
    if col.button(name, use_container_width=True,
                  type="primary" if st.session_state["universe"] == name else "secondary",
                  key=f"u_{name}"):
        st.session_state["universe"] = name
        st.rerun()
universe_choice = st.session_state["universe"]

uploaded = None

st.session_state.setdefault("rsi_thr", 65)
st.markdown('<div class="sec-label" style="margin-top:16px">Screen filters</div>',
            unsafe_allow_html=True)
fc1, fc2 = st.columns([3, 2], gap="large")
threshold = fc1.slider("RSI at or above", 0, 90, key="rsi_thr")
fetch_limit = fc2.number_input("Load details for top N matches", 10, 500, 100, 10)

bc1, bc2, _bc3 = st.columns([1, 1, 4], gap="small")
run = bc1.button("Run screen", type="primary", use_container_width=True)
clear = bc2.button("Clear", use_container_width=True)

if universe_choice == "All NIFTY Stocks":
    st.caption("All NIFTY Stocks pulls ~2000 listed stocks and can take several "
               "minutes. NIFTY 500 is the better broad option for daily use.")

if clear:
    for k in ("results", "scanned", "threshold", "opened_for", "screener_table",
              "qp_opened", "rsi_thr", "universe", "view"):
        st.session_state.pop(k, None)
    st.query_params.clear()
    st.rerun()

if run:
    tickers = tickers_for(universe_choice, uploaded)
    if not tickers:
        st.error(f"The {universe_choice} list did not load. Try again in a minute.")
        st.stop()
    st.session_state["results"] = scan(tuple(tickers), float(threshold), int(fetch_limit))
    st.session_state["scanned"] = len(tickers)
    st.session_state["threshold"] = threshold
    st.session_state["opened_for"] = None
    st.query_params["scan"] = _cfg_str(universe_choice, threshold, int(fetch_limit))
    if "stock" in st.query_params:
        del st.query_params["stock"]
    st.session_state.pop("qp_opened", None)

# restore the results table after a ?stock= hyperlink reload (cached -> instant)
if st.session_state.get("results") is None:
    _cfg = st.query_params.get("scan")
    if _cfg:
        try:
            _u, _th, _n = _cfg.split(SCAN_SEP)
            _th, _n = float(_th), int(_n)
            _tks = tickers_for(_u)
            if _tks:
                st.session_state["results"] = scan(tuple(_tks), _th, _n)
                st.session_state["scanned"] = len(_tks)
                st.session_state["threshold"] = _th
                st.session_state["universe"] = _u
        except Exception:
            pass

results = st.session_state.get("results")

if results is None:
    st.info("Pick a stock list, set your RSI threshold, then run the screen. "
            "You can also look up any single stock from the sidebar.")
elif results.empty:
    st.warning(f"Nothing is at RSI {st.session_state.get('threshold', threshold)} or "
               "above right now. Lower the threshold or widen the stock list.")
else:
    hleft, hright = st.columns([3, 1])
    hleft.markdown(f"**{len(results)} stocks** at RSI "
                   f"{st.session_state.get('threshold', threshold)} or above, "
                   f"from {st.session_state.get('scanned', 0)} scanned.")
    hright.download_button("Download report", data=to_excel_bytes(results),
                           file_name=f"RSI_Screener_{dt.date.today().isoformat()}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet", use_container_width=True)

    def _fmt(x):
        return f"{x:,.2f}" if isinstance(x, (int, float)) else str(x)

    aligns = ["l", "l", "l", "r", "r", "r", "r", "r", "c", "r", "r"]
    head_html = "".join(
        f'<th class="th-{a}">{COL_LABELS.get(h, h)}</th>' for h, a in zip(HEADERS, aligns))

    _cfgq = quote(st.query_params.get("scan", ""), safe="")
    body_rows = []
    for _, r in results.iterrows():
        sym = r["Stock Symbol"]
        rsi = r["RSI"]
        reco = r["Buy/Sell"]
        rsi_txt = f"{rsi:.1f}" if isinstance(rsi, (int, float)) else str(rsi)
        href = f"?scan={_cfgq}&stock={sym}" if _cfgq else f"?stock={sym}"
        _sv = lambda v: f"{v:,.2f}" if isinstance(v, (int, float)) else "n/a"
        _vol = lambda v: f"{int(v):,}" if isinstance(v, (int, float)) else "n/a"
        sma_tip = (f"20-day SMA: {_sv(r.get('_SMA20'))}  |  "
                   f"50-day SMA: {_sv(r.get('_SMA50'))}  |  "
                   f"200-day SMA: {_sv(r.get('_SMA200'))}")
        _pe = _sv(r.get("PE"))
        _secpe = _sv(r.get("Sec PE"))
        body_rows.append(
            "<tr>"
            f'<td class="c-date">{r["Date"]}</td>'
            f'<td><a class="c-sym" href="{href}" target="_self">{sym}</a></td>'
            f'<td class="c-sec" style="color:{sector_color(r["Sector"])}">{r["Sector"]}</td>'
            f'<td class="c-num c-price" title="{sma_tip}">{_fmt(r["Current Price (Rs)"])}</td>'
            f'<td class="c-num c-muted">{_vol(r.get("Volume"))}</td>'
            f'<td class="c-num c-rsi" style="color:{rsi_color(rsi)}">{rsi_txt}</td>'
            f'<td class="c-num">{_pe}</td>'
            f'<td class="c-num c-muted">{_secpe}</td>'
            f'<td class="c-reco"><span class="pill" title="{reco_info(reco)}" '
            f'style="background:{reco_color(reco)}">{reco}</span></td>'
            f'<td class="c-num c-muted">{_fmt(r["52 Week High (Rs)"])}</td>'
            f'<td class="c-num">{_fmt(r["1 Year Target (Rs)"])}</td>'
            "</tr>")

    st.markdown('<div class="sh-hint">Click a stock symbol for its full detail. '
                "Hover the underlined price to see 20 / 50 / 200-day moving averages."
                "</div>", unsafe_allow_html=True)
    st.markdown(
        '<div class="sh-tablewrap"><table class="sh-table"><thead><tr>'
        + head_html + "</tr></thead><tbody>" + "".join(body_rows)
        + "</tbody></table></div>", unsafe_allow_html=True)
    na = int((results["Buy/Sell"] == "n/a").sum())
    if na:
        st.caption(f"{na} of {len(results)} stocks have no published analyst rating — "
                   "common for smaller companies.")

st.markdown(f'<div class="disc"><strong>Disclaimer</strong> — {DISCLAIMER}</div>',
            unsafe_allow_html=True)
