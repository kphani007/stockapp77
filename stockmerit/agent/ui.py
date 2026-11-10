"""Streamlit view for the AI Analyst. The only Streamlit-specific file in this package.

Called from streamlit_app.py as:
    agent_ui.render(_secret, extra_tools=[...], disclaimer=DISCLAIMER)

Secrets used (all read through the `secret` callable the app passes in):
    ANTHROPIC_API_KEY   required for Chat and Daily report
    AGENT_ACCESS_CODE   required: the code a visitor must enter to use the model.
                        Fails closed: with no code set, Chat and Daily report stay off,
                        so a public deployment cannot spend the API key by accident.
    AGENT_MODEL         optional model id override
    AGENT_WEB_SEARCH    optional, "0" turns web search off
    AGENT_MAX_TURNS     optional per-session cap on model turns (default 40)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Callable

import pandas as pd
import streamlit as st

from . import data, options, profile, risk
from .core import DAILY_REPORT_PROMPT, Agent

TABS = ["Chat", "Daily report", "My profile", "Calculators"]
MAX_UNLOCK_TRIES = 5
AGENT_NOTE = ("AI-generated research notes, not advice from a SEBI-registered adviser. Prices "
              "are delayed; confirm every level on your broker terminal. The model can be "
              "wrong. You are responsible for your own trades.")
SUGGESTIONS = [
    "Give me today's market overview with Nifty and Bank Nifty levels.",
    "Scan the watchlist for swing setups and show the top three.",
    "Is HDFCBANK a long-term accumulate candidate? Show what data is missing.",
]


def _md(text: str) -> str:
    return text.replace("$", "\\$")  # stop Streamlit treating $...$ as LaTeX


def _state() -> None:
    ss = st.session_state
    ss.setdefault("agent_profile", {})
    ss.setdefault("agent_history", [])
    ss.setdefault("agent_turns", 0)
    ss.setdefault("agent_unlocked", False)
    ss.setdefault("agent_unlock_tries", 0)


def _int_secret(secret: Callable[[str], str], name: str, default: int) -> int:
    try:
        return int(secret(name) or default)
    except ValueError:
        return default


def _model_ready(secret: Callable[[str], str]) -> bool:
    """Gate for anything that spends API credit. Renders its own explanation."""
    ss = st.session_state
    if not secret("ANTHROPIC_API_KEY"):
        st.info("The AI Analyst is not configured. Add `ANTHROPIC_API_KEY` and "
                "`AGENT_ACCESS_CODE` to the app secrets. The Calculators and My profile "
                "tabs work without them.")
        return False
    code = secret("AGENT_ACCESS_CODE")
    if not code:
        st.warning("`AGENT_ACCESS_CODE` is not set in the app secrets, so the AI Analyst is "
                   "switched off. Set one to enable it. This stops visitors to a public "
                   "deployment from spending your API key.")
        return False
    if ss["agent_unlocked"]:
        return True
    if ss["agent_unlock_tries"] >= MAX_UNLOCK_TRIES:
        st.error("Too many incorrect attempts in this session.")
        return False
    with st.form("agent_unlock"):
        entered = st.text_input("Access code", type="password")
        if st.form_submit_button("Unlock AI Analyst", type="primary"):
            if hmac.compare_digest(entered.encode(), code.encode()):
                ss["agent_unlocked"] = True
                st.rerun()
            ss["agent_unlock_tries"] += 1
            st.error("Incorrect access code.")
    return False


def _new_agent(secret, extra_tools, on_event=None) -> Agent:
    return Agent(api_key=secret("ANTHROPIC_API_KEY"),
                 profile_store=st.session_state["agent_profile"],
                 extra_tools=extra_tools, on_event=on_event,
                 model=secret("AGENT_MODEL") or None,
                 web_search=secret("AGENT_WEB_SEARCH") != "0")


def _run(agent: Agent, prompt: str, secret) -> str | None:
    """One model turn with a live activity log. Returns the answer, or None on failure."""
    ss = st.session_state
    cap = _int_secret(secret, "AGENT_MAX_TURNS", 40)
    if ss["agent_turns"] >= cap:
        st.warning(f"Session limit of {cap} AI requests reached. Reload the page to start again.")
        return None
    ss["agent_turns"] += 1
    with st.status("Working: fetching data and checking the setup…", expanded=False) as box:
        def log(kind: str, detail: str) -> None:
            box.write(("Web search: " if kind == "search" else "Tool: ") + detail)
        agent.on_event = log
        try:
            text = agent.ask(prompt)
        except Exception as e:  # never show a traceback or key material to the visitor
            box.update(label="Request failed", state="error")
            st.error(f"The AI request failed ({type(e).__name__}). Check the API key, the "
                     "model id and your API credit, then try again.")
            if agent.messages and agent.messages[-1].get("role") == "user":
                agent.messages.pop()  # keep history valid for the next attempt
            return None
        finally:
            agent.on_event = None
        box.update(label="Done", state="complete")
    return text


# ----------------------------------------------------------------------- tabs
def _chat(secret, extra_tools) -> None:
    ss = st.session_state
    if not _model_ready(secret):
        return
    if "agent_obj" not in ss:
        ss["agent_obj"] = _new_agent(secret, extra_tools)
    missing = profile.status(ss["agent_profile"])["missing_fields"]
    if missing:
        st.caption(f"Profile incomplete ({len(missing)} of {len(profile.FIELDS)} fields missing). "
                   "The analyst will ask before recommending a trade, or fill in My profile.")
    picked = None
    if not ss["agent_history"]:
        cols = st.columns(len(SUGGESTIONS))
        for i, (c, s) in enumerate(zip(cols, SUGGESTIONS)):
            if c.button(s, key=f"agent_sugg_{i}", use_container_width=True):
                picked = s
    for m in ss["agent_history"]:
        with st.chat_message(m["role"]):
            st.markdown(_md(m["text"]))
    typed = st.chat_input("Ask about a stock, a setup, or a strategy")
    prompt = typed or picked or ss.pop("agent_prefill", None)
    if prompt:
        prompt = prompt.strip()[:4000]
        ss["agent_history"].append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(_md(prompt))
        with st.chat_message("assistant"):
            text = _run(ss["agent_obj"], prompt, secret)
            if text is None:
                ss["agent_history"].pop()
            else:
                st.markdown(_md(text))
                ss["agent_history"].append({"role": "assistant", "text": text})
    if ss["agent_history"]:
        if st.button("Clear conversation", key="agent_clear"):
            ss["agent_history"] = []
            ss.pop("agent_obj", None)
            st.rerun()


def _report(secret, extra_tools) -> None:
    ss = st.session_state
    st.caption("Market overview, stocks to watch, intraday, swing and F&O ideas, risk "
               "assessment and a ranked final view. Takes a minute or two and uses several "
               "data and web-search calls.")
    if not _model_ready(secret):
        return
    if st.button("Generate today's report", type="primary", key="agent_report_btn"):
        text = _run(_new_agent(secret, extra_tools), DAILY_REPORT_PROMPT, secret)
        if text is not None:
            ss["agent_report"] = {"text": text, "at": data.now_ist()}
    rep = ss.get("agent_report")
    if rep:
        st.caption(f"Generated {rep['at']}")
        with st.container(border=True):
            st.markdown(_md(rep["text"]))
        st.download_button("Download report (.md)", rep["text"],
                           "stockmerit_daily_report.md", "text/markdown", key="agent_report_dl")


def _profile_tab() -> None:
    p = st.session_state["agent_profile"]
    st.caption("The analyst sizes and filters trades from this. It is kept for this browser "
               "session only; download it to reuse next time.")

    def pick(label, opts, key):
        cur = p.get(key)
        return st.selectbox(label, opts, index=opts.index(cur) if cur in opts else None,
                            placeholder="Choose…")

    with st.form("agent_profile_form"):
        c1, c2 = st.columns(2)
        capital = c1.number_input("Trading capital (₹)", min_value=0.0, step=10000.0, format="%.0f",
                                  value=float(p.get("trading_capital_inr") or 0.0))
        daily = c2.number_input("Maximum daily loss (₹)", min_value=0.0, step=500.0, format="%.0f",
                                value=float(p.get("max_daily_loss_inr") or 0.0))
        c3, c4 = st.columns(2)
        with c3:
            risk_pct = st.number_input("Risk per trade (% of capital)", min_value=0.0,
                                       max_value=risk.HARD_MAX_RISK_PCT, step=0.25, format="%.2f",
                                       value=float(p.get("risk_pct_per_trade") or 0.0),
                                       help="0.5 to 1 is the conservative starting range.")
            tol = pick("Risk tolerance", ["conservative", "moderate", "aggressive"], "risk_tolerance")
        with c4:
            exp = pick("Trading experience", ["beginner", "intermediate", "experienced"],
                       "trading_experience")
            horizon = st.text_input("Investment horizon", value=p.get("investment_horizon", ""),
                                    placeholder="e.g. swing 2-4 weeks, long-term 5+ years")
        seg_opts = ["delivery", "swing", "intraday", "futures", "options buying",
                    "options selling (defined risk)"]
        cur = [s.strip() for s in str(p.get("segments_allowed", "")).split(",") if s.strip() in seg_opts]
        segs = st.multiselect("What do you want ideas for?", seg_opts, default=cur)
        style = st.text_input("Preferred style", value=p.get("preferred_style", ""),
                              placeholder="e.g. positional, swing")
        prefs = st.text_input("Preferred or excluded sectors and stocks",
                              value=p.get("preferred_sectors_or_stocks", ""))
        if st.form_submit_button("Save profile", type="primary"):
            raw = {"trading_capital_inr": capital or None, "max_daily_loss_inr": daily or None,
                   "risk_pct_per_trade": risk_pct or None, "risk_tolerance": tol,
                   "trading_experience": exp, "investment_horizon": horizon,
                   "segments_allowed": ", ".join(segs), "preferred_style": style,
                   "preferred_sectors_or_stocks": prefs}
            ok, _ = profile.clean({k: v for k, v in raw.items() if v not in (None, "")})
            p.clear()
            p.update(ok)
            st.success("Profile saved for this session.")
    missing = profile.status(p)["missing_fields"]
    if missing:
        st.caption("Still missing: " + ", ".join(missing.values()))
    c5, c6 = st.columns(2)
    c5.download_button("Download profile (.json)", json.dumps(p, indent=2),
                       "stockmerit_profile.json", "application/json", disabled=not p,
                       key="agent_profile_dl")
    up = c6.file_uploader("Restore a saved profile", type=["json"], key="agent_profile_up")
    raw = up.getvalue() if up is not None else b""
    digest = hashlib.sha256(raw).hexdigest() if up is not None else None
    if up is not None and st.session_state.get("agent_profile_loaded") != digest:
        try:
            if len(raw) > 20_000:
                raise ValueError
            loaded = json.loads(raw.decode("utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError
            ok, bad = profile.clean(loaded)
            p.clear()
            p.update(ok)
            st.session_state["agent_profile_loaded"] = digest
            st.success(f"Loaded {len(ok)} fields." + (f" Ignored: {', '.join(bad)}." if bad else ""))
            st.rerun()
        except (ValueError, UnicodeDecodeError):
            st.error("That file is not a valid StockMerit profile.")


def _calculators() -> None:
    st.caption("Arithmetic only, no AI. Enter real numbers from your broker terminal.")
    with st.expander("Charges estimate (round trip)", expanded=True):
        cfg = risk.load_charges()
        c1, c2, c3, c4 = st.columns(4)
        seg = c1.selectbox("Segment", list(cfg["segments"]), key="calc_seg")
        buy = c2.number_input("Buy price (₹)", min_value=0.0, value=500.0, step=0.05, key="calc_buy")
        sell = c3.number_input("Sell price (₹)", min_value=0.0, value=505.0, step=0.05, key="calc_sell")
        qty = c4.number_input("Quantity", min_value=1, value=100, step=1, key="calc_qty",
                              help="For futures and options: lots × lot size. Use the premium as price for options.")
        r = risk.estimate_charges(seg, buy, sell, qty)
        m1, m2, m3 = st.columns(3)
        m1.metric("Total charges", f"₹{r['total_charges']:,.2f}")
        m2.metric("Gross P&L", f"₹{r['gross_pnl']:,.2f}")
        m3.metric("Net P&L", f"₹{r['net_pnl']:,.2f}")
        st.caption(" · ".join(f"{k.replace('_', ' ')} ₹{r[k]:,.2f}" for k in
                              ("brokerage", "stt", "exchange_txn", "sebi", "stamp_duty", "gst")))
        st.caption(f"Rates last reviewed {r['rates_last_reviewed']} (stockmerit/agent/config/"
                   "charges.json). Brokerage is a typical discount-broker pattern; slippage is "
                   "not included. Check against your broker's contract note.")

    with st.expander("Option strategy payoff at expiry"):
        st.caption("Example legs shown: a bull call spread. Replace with your own strikes and "
                   "live premiums. FUT legs use the premium column as the futures entry price.")
        default = pd.DataFrame([
            {"type": "CE", "side": "BUY", "strike": 100.0, "premium": 5.0},
            {"type": "CE", "side": "SELL", "strike": 110.0, "premium": 2.0}])
        legs_df = st.data_editor(
            default, num_rows="dynamic", key="calc_legs", use_container_width=True,
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=["CE", "PE", "FUT"], required=True),
                "side": st.column_config.SelectboxColumn("Side", options=["BUY", "SELL"], required=True),
                "strike": st.column_config.NumberColumn("Strike", min_value=0.0),
                "premium": st.column_config.NumberColumn("Premium / price", min_value=0.0)})
        c1, c2 = st.columns(2)
        lot = c1.number_input("Lot size (verify on NSE or your broker)", min_value=1, value=1,
                              step=1, key="calc_lot")
        lots = c2.number_input("Lots", min_value=1, value=1, step=1, key="calc_lots")
        legs = []
        for row in legs_df.to_dict("records"):
            if not row.get("type") or not row.get("side") or pd.isna(row.get("premium")):
                continue
            if row["type"] == "FUT":
                legs.append({"type": "FUT", "side": row["side"], "price": float(row["premium"])})
            elif not pd.isna(row.get("strike")):
                legs.append({"type": row["type"], "side": row["side"],
                             "strike": float(row["strike"]), "premium": float(row["premium"])})
        res = options.strategy_payoff(legs, int(lot), int(lots))
        if "error" in res:
            st.info("Add at least one complete leg.")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Max profit", "Unlimited" if res["max_profit_unlimited"]
                      else f"₹{res['max_profit']:,.2f}")
            m2.metric("Max loss", "Unlimited" if res["max_loss_unlimited"]
                      else f"₹{res['max_loss']:,.2f}")
            m3.metric("Breakeven", ", ".join(f"{b:,.2f}" for b in res["breakevens"]) or "none")
            st.caption(f"Net {res['net_premium_type']} per unit ₹{abs(res['net_premium_per_unit']):,.2f}"
                       + (f" · reward : risk {res['reward_to_risk']}" if res["reward_to_risk"] else "")
                       + " · before charges · margin not computed")
            if res.get("warning"):
                st.error("Uncovered short option: this structure has a short option that is not "
                         "fully hedged by a long option. Losses can be very large or unlimited.")

    with st.expander("Option Greeks (Black-Scholes, theoretical)"):
        c1, c2, c3 = st.columns(3)
        spot = c1.number_input("Spot", min_value=0.01, value=100.0, key="g_spot")
        strike = c2.number_input("Strike", min_value=0.01, value=100.0, key="g_strike")
        typ = c3.selectbox("Type", ["CE", "PE"], key="g_type")
        c4, c5, c6 = st.columns(3)
        days = c4.number_input("Days to expiry", min_value=0.1, value=7.0, key="g_days")
        iv = c5.number_input("Implied volatility (%)", min_value=0.1, value=15.0, key="g_iv",
                             help="Take this from your option chain.")
        rate = c6.number_input("Risk-free rate (%)", min_value=0.0, value=6.5, key="g_rate",
                               help="Placeholder default. Set to the current rate you use.")
        g = options.bs_greeks(spot, strike, days, iv, typ, rate)
        m = st.columns(5)
        for col, (label, key) in zip(m, [("Model price", "model_price"), ("Delta", "delta"),
                                         ("Gamma", "gamma"), ("Theta / day", "theta_per_day"),
                                         ("Vega / 1% IV", "vega_per_1pct_iv")]):
            col.metric(label, g[key])
        st.caption("Theoretical values from the inputs above. Not a market quote.")


# ---------------------------------------------------------------------- entry
def render(secret: Callable[[str], str], extra_tools: list | None = None,
           disclaimer: str = "") -> None:
    _state()
    ss = st.session_state
    st.markdown("### AI Analyst")
    st.caption("A research assistant that screens, scores and sizes setups using StockMerit's "
               "data tools, and says NO TRADE when nothing qualifies.")
    if "_agent_pending_tab" in ss:
        ss["agent_tab"] = ss.pop("_agent_pending_tab")
    tab = st.radio("section", TABS, horizontal=True, label_visibility="collapsed", key="agent_tab")
    if tab == "Chat":
        _chat(secret, extra_tools)
    elif tab == "Daily report":
        _report(secret, extra_tools)
    elif tab == "My profile":
        _profile_tab()
    else:
        _calculators()
    st.markdown(f'<div class="disc"><strong>Disclaimer</strong> — {AGENT_NOTE} {disclaimer}</div>',
                unsafe_allow_html=True)
