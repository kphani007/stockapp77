import json, types
import numpy as np, pandas as pd, pytest
from stockmerit.agent import analysis, backtest, data, indicators as ind, options, risk, tools, profile


def make(n=600, drift=0.0008, seed=1, vol=2e6):
    rng = np.random.default_rng(seed)
    c = 1000 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.004, n)))
    l = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.004, n)))
    idx = pd.bdate_range("2023-01-02", periods=n, tz="Asia/Kolkata")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                         "volume": rng.integers(vol // 2, vol * 2, n).astype(float)}, index=idx)


class Fake:
    name = "fake"
    def history(self, t, period, interval):
        if t == "BAD.NS":
            return pd.DataFrame()
        df = make(seed=abs(hash(t)) % 99)
        return df.rename(columns=str.capitalize)
    def info(self, t):
        return {"trailingPE": 22.5, "sector": "Energy"}


@pytest.fixture(autouse=True)
def fake():
    data.set_provider(Fake())
    yield
    data.set_provider(data.YFinanceProvider())


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100
    assert ind.rsi(up[::-1].reset_index(drop=True)).iloc[-1] == pytest.approx(0, abs=1e-9)
    r = ind.rsi(make()["close"]).dropna()
    assert r.between(0, 100).all()


def test_ema_matches_manual():
    s = pd.Series([1.0, 2, 3, 4, 5, 6])
    k = 2 / 4
    m = [1.0]
    for x in s[1:]:
        m.append(x * k + m[-1] * (1 - k))
    assert ind.ema(s, 3).iloc[-1] == pytest.approx(m[-1])


def test_adx_atr_supertrend_sane():
    df = make()
    a = ind.adx(df).dropna()
    assert a["adx"].between(0, 100).all()
    assert (ind.atr(df).dropna() > 0).all()
    st = ind.supertrend(df).dropna()
    assert set(st["direction"].unique()) <= {1.0, -1.0}
    up = st["direction"] == 1
    assert (st["supertrend"][up] <= df["close"][st.index][up] + 1e-9).all()


def test_pivots_and_vwap():
    p = ind.pivot_levels(110, 90, 100)
    assert p["pivot"] == 100 and p["r1"] == 110 and p["s1"] == 90 and p["r2"] == 120
    idx = pd.date_range("2026-11-09 09:15", periods=4, freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({"high": [10, 20, 30, 40.0], "low": [10, 20, 30, 40.0],
                       "close": [10, 20, 30, 40.0], "volume": [1, 1, 2, 0.0]}, index=idx)
    assert ind.session_vwap(df).iloc[2] == pytest.approx((10 + 20 + 60) / 4)


def test_position_size_formula_and_guards():
    r = risk.position_size(500000, 250, 245, risk_pct=1)
    assert r["quantity"] == 1000 and r["actual_rupee_risk_at_stop"] == 5000
    assert "error" in risk.position_size(500000, 250, 245, risk_pct=5)
    assert risk.position_size(100000, 100, 99, risk_pct=1)["quantity"] == 1000  # capped by capital
    r = risk.position_size(200000, 150, 120, risk_pct=1, lot_size=75)
    assert r["quantity"] == 0 and r["decision"] == "NO TRADE"
    r = risk.position_size(500000, 250, 245, risk_pct=1, max_daily_loss=6000, loss_today=6000)
    assert r["decision"] == "NO TRADE"
    r = risk.position_size(500000, 250, 245, risk_pct=1, max_daily_loss=6000, loss_today=4000)
    assert r["quantity"] == 400


def test_charges_intraday_manual():
    c = risk.estimate_charges("intraday", 100, 101, 1000)
    assert c["stt"] == pytest.approx(101000 * 0.00025)
    assert c["brokerage"] == 40.0
    assert c["stamp_duty"] == pytest.approx(3.0)
    assert c["net_pnl"] == pytest.approx(1000 - c["total_charges"])


def test_bull_call_spread():
    r = options.strategy_payoff([{"type": "CE", "side": "BUY", "strike": 100, "premium": 5},
                                 {"type": "CE", "side": "SELL", "strike": 110, "premium": 2}], 50)
    assert r["max_loss"] == -150 and r["max_profit"] == 350 and r["breakevens"] == [103.0]
    assert r["defined_risk"] and "warning" not in r


def test_iron_condor_and_naked():
    legs = [{"type": "PE", "side": "BUY", "strike": 90, "premium": 1},
            {"type": "PE", "side": "SELL", "strike": 95, "premium": 2},
            {"type": "CE", "side": "SELL", "strike": 105, "premium": 2},
            {"type": "CE", "side": "BUY", "strike": 110, "premium": 1}]
    r = options.strategy_payoff(legs, 10)
    assert r["max_profit"] == 20 and r["max_loss"] == -30 and r["breakevens"] == [93.0, 107.0]
    n = options.strategy_payoff([{"type": "CE", "side": "SELL", "strike": 100, "premium": 3}], 10)
    assert n["max_loss_unlimited"] and not n["defined_risk"] and "warning" in n
    p = options.strategy_payoff([{"type": "PE", "side": "SELL", "strike": 100, "premium": 3}], 10)
    assert not p["defined_risk"] and p["max_loss"] == -970
    lc = options.strategy_payoff([{"type": "CE", "side": "BUY", "strike": 100, "premium": 3}], 10)
    assert lc["max_profit_unlimited"] and lc["max_loss"] == -30 and lc["breakevens"] == [103.0]


def test_greeks_parity():
    c = options.bs_greeks(100, 100, 30, 20, "CE", 6)
    p = options.bs_greeks(100, 100, 30, 20, "PE", 6)
    t = 30 / 365
    assert c["model_price"] - p["model_price"] == pytest.approx(100 - 100 * np.exp(-0.06 * t), abs=0.02)
    assert c["delta"] - p["delta"] == pytest.approx(1, abs=1e-3)


def test_score_missing_data_not_manufactured():
    df, b = make(), make(seed=7)
    s = analysis.score_setup(df, b, "long")
    assert s["components"]["risk_reward"]["points"] is None
    assert s["score_out_of_100"] is None and s["points_scorable"] == 90
    s2 = analysis.score_setup(df, b, "long", entry=100, stop=95, target=115)
    assert s2["score_out_of_100"] is not None and s2["components"]["risk_reward"]["points"] == 10
    assert 0 <= s2["score_out_of_100"] <= 100
    short = analysis.score_setup(df.iloc[:100], None, "long")
    assert {"trend", "sector_market"} <= set(short["unscored_components"])
    assert not short["passes_threshold"]
    z = df.assign(volume=0.0)
    assert analysis.score_setup(z, b)["components"]["volume"]["points"] is None


def test_score_direction_sensitive():
    up, b = make(drift=0.004, seed=3), make(seed=7)
    lo = analysis.score_setup(up, b, "long")["components"]["trend"]["points"]
    sh = analysis.score_setup(up, b, "short")["components"]["trend"]["points"]
    assert lo > sh


def test_backtest_runs_and_costs_hurt():
    df = make(n=1500, drift=0.0006)
    a = backtest.run(df, "ema_pullback", cost_pct_per_side=0, slippage_pct_per_side=0)
    b = backtest.run(df, "ema_pullback", cost_pct_per_side=0.5, slippage_pct_per_side=0.2)
    assert a["overall"]["trades"] > 0
    assert b["overall"]["expectancy_pct_per_trade"] < a["overall"]["expectancy_pct_per_trade"]
    assert "error" in backtest.run(df.iloc[:100])
    assert backtest.run(df, "breakout_20d")["strategy"] == "breakout_20d"


def test_tools_dispatch_and_errors():
    r = json.loads(tools.run_tool("get_technical_analysis", {"symbol": "RELIANCE"}))
    assert r["symbol"] == "RELIANCE.NS" and "last_bar_time" in r and r["ema200"] is not None
    bad = json.loads(tools.run_tool("get_technical_analysis", {"symbol": "BAD"}))
    assert "error" in bad and "do not estimate" in bad["instruction"]
    assert "error" in json.loads(tools.run_tool("nope", {}))
    f = json.loads(tools.run_tool("get_fundamentals", {"symbol": "RELIANCE"}))
    assert f["fields"]["pe_trailing"] == 22.5 and "promoter_pledging" in f["unavailable_from_feed"]
    sc = json.loads(tools.run_tool("scan_universe", {"symbols": ["A", "B", "BAD"], "top_n": 2}))
    assert sc["scanned"] == 2 and "BAD" in sc["failed"]
    snap = json.loads(tools.run_tool("get_market_snapshot", {}))
    assert "NIFTY" in snap["instruments"]
    i = json.loads(tools.run_tool("get_technical_analysis", {"symbol": "X", "timeframe": "weekly"}))
    assert "error" not in i


def test_profile_is_per_session_and_validated():
    s1, s2 = {}, {}
    assert "trading_capital_inr" in profile.status(s1)["missing_fields"]
    r = json.loads(tools.run_tool("update_profile", {"fields": {"trading_capital_inr": 500000,
                                  "bogus": 1, "max_daily_loss_inr": "abc"}}, s1))
    assert s1 == {"trading_capital_inr": 500000.0} and s2 == {}
    assert set(r["rejected_fields"]) == {"bogus", "max_daily_loss_inr"}
    assert json.loads(tools.run_tool("get_profile", {}, s2))["profile"] == {}
    assert len(profile.clean({"preferred_style": "x" * 999})[0]["preferred_style"]) == profile.MAX_TEXT


def test_extra_tool_and_rsi_shared_with_screener():
    from stockmerit.features.momentum import rolling_rsi
    out = json.loads(tools.run_tool("get_futures_oi_buildup", {}, {}, {"get_futures_oi_buildup": lambda: {"source": "yf"}}))
    assert out == {"source": "yf"}
    c = make()["close"]
    pd.testing.assert_series_equal(ind.rsi(c), rolling_rsi(c))


def test_agent_loop_with_stub_client(monkeypatch):
    from stockmerit.agent import core as ag
    B = types.SimpleNamespace
    calls = []
    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                return B(stop_reason="tool_use", content=[
                    B(type="tool_use", id="t1", name="position_size",
                      input={"capital": 100000, "entry": 100, "stop": 98, "risk_pct": 1})])
            return B(stop_reason="end_turn", content=[B(type="text", text="done", citations=None)])
    events, store = [], {}
    a = ag.Agent(client=B(messages=Msgs()), profile_store=store, on_event=lambda k, d: events.append(k))
    assert a.ask("size it") == "done" and events == ["tool"]
    tr = a.messages[2]["content"][0]
    assert tr["type"] == "tool_result" and json.loads(tr["content"])["quantity"] == 500
    assert any(t.get("name") == "web_search" for t in calls[0]["tools"])


def test_agent_against_real_sdk_with_mock_transport():
    """Real anthropic SDK, fake HTTP: checks request serialisation and response parsing."""
    anthropic = pytest.importorskip("anthropic")
    try:
        import httpx2 as httpx   # newer SDK releases
    except ImportError:
        httpx = pytest.importorskip("httpx")
    from stockmerit.agent import core as ag
    seen = []
    def handler(req):
        body = json.loads(req.content); seen.append(body)
        base = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
        if len(seen) == 1:
            base.update(stop_reason="tool_use", content=[
                {"type": "text", "text": "Sizing. "},
                {"type": "tool_use", "id": "toolu_1", "name": "estimate_charges",
                 "input": {"segment": "delivery", "buy_price": 100, "sell_price": 110, "quantity": 10}}])
        else:
            base.update(stop_reason="end_turn", content=[{"type": "text", "text": "Done."}])
        return httpx.Response(200, json=base)
    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = ag.Agent(client=client).ask("charges?")
    assert out == "Sizing. Done."
    second = seen[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][1]["type"] == "tool_use"
    assert second[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert seen[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert seen[0]["tools"][-1] == {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}


# ------------------------------------------------------------------ Streamlit view
def _app(secrets):
    from streamlit.testing.v1 import AppTest
    def script(secrets):
        import streamlit as st
        from stockmerit.agent import ui
        ui.render(lambda n: secrets.get(n, ""), disclaimer="x")
    return AppTest.from_function(script, kwargs={"secrets": secrets}, default_timeout=30)


def test_ui_fails_closed_without_key_or_code():
    at = _app({}).run()
    assert not at.exception and "not configured" in at.info[0].value
    assert not at.chat_input
    at = _app({"ANTHROPIC_API_KEY": "k"}).run()
    assert not at.exception and "AGENT_ACCESS_CODE" in at.warning[0].value and not at.chat_input


def test_ui_access_code_gate():
    at = _app({"ANTHROPIC_API_KEY": "k", "AGENT_ACCESS_CODE": "s3cret"}).run()
    assert not at.chat_input
    at.text_input[0].set_value("wrong"); at.button[0].click(); at.run()
    assert "Incorrect" in at.error[0].value and not at.chat_input
    at.text_input[0].set_value("s3cret"); at.button[0].click(); at.run()
    assert not at.exception and at.chat_input


def test_ui_profile_and_calculators_work_without_key():
    at = _app({}).run()
    at.radio[0].set_value("Calculators").run()
    assert not at.exception
    vals = {m.label: m.value for m in at.metric}
    assert vals["Max profit"] == "₹7.00" and vals["Max loss"] == "₹-3.00" and vals["Breakeven"] == "103.00"
    at.radio[0].set_value("My profile").run()
    assert not at.exception
    at.number_input[0].set_value(500000.0)
    at.button[0].click(); at.run()
    assert not at.exception
    assert at.session_state["agent_profile"]["trading_capital_inr"] == 500000.0
