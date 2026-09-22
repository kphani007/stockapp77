"""Unit tests for the stockmerit package -- all synthetic data, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockmerit.features import fundamentals, momentum, relative_strength, trend, volatility, volume
from stockmerit.scoring import backtest
from stockmerit.scoring.merit_score import DEFAULT_WEIGHTS, MeritComponents, compute_merit_score


def _dates(n: int, start: str = "2023-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def _series(values, start: str = "2023-01-02") -> pd.Series:
    return pd.Series(values, index=_dates(len(values), start))


# --------------------------------- momentum ---------------------------------

def test_rolling_rsi_bounds():
    close = _series(list(np.linspace(100, 130, 60)))  # steady uptrend
    rsi = momentum.rolling_rsi(close)
    valid = rsi.dropna()
    assert not valid.empty
    assert (valid >= 0).all() and (valid <= 100).all()
    assert valid.iloc[-1] > 60  # sustained uptrend -> high RSI


# -------------------------------- volatility ---------------------------------

def test_bollinger_score_rewards_a_squeeze():
    rng = np.random.default_rng(0)
    wide = 100 + np.cumsum(rng.normal(0, 2.0, 150))
    tight = np.concatenate([wide, wide[-1] + np.cumsum(rng.normal(0, 0.05, 30))])
    close = _series(tight)
    score = volatility.bollinger_score(close)
    assert score is not None
    assert score > 50  # the tail is a tighter squeeze than its own recent history


def test_bollinger_score_none_on_short_history():
    close = _series([100.0] * 10)
    assert volatility.bollinger_score(close) is None


# ---------------------------------- volume ------------------------------------

def test_volume_trend_expansion_and_contraction():
    base = [1000.0] * 20
    expansion = volume.volume_trend(_series(base[:-5] + [5000.0] * 5))
    contraction = volume.volume_trend(_series(base[:-5] + [100.0] * 5))
    assert expansion > 1.0
    assert contraction < 1.0
    assert volume.volume_trend_score(expansion) > 50
    assert volume.volume_trend_score(contraction) < 50


def test_accumulation_score_up_vs_down():
    n = 40
    dates = _dates(n)
    up_close = pd.Series(np.linspace(100, 140, n), index=dates)
    up_high = up_close + 0.1
    up_low = up_close - 0.5  # small gap to the high, large gap to the low -> closes near the high
    vol = pd.Series(np.linspace(1000, 3000, n), index=dates)
    up_score = volume.accumulation_score(up_high, up_low, up_close, vol)

    down_close = pd.Series(np.linspace(140, 100, n), index=dates)
    down_high = down_close + 0.5
    down_low = down_close - 0.1  # small gap to the low, large gap to the high -> closes near the low
    down_score = volume.accumulation_score(down_high, down_low, down_close, vol)

    assert up_score > 50
    assert down_score < 50


# ----------------------------- relative strength -------------------------------

def test_relative_strength_score_outperformance():
    n = 80
    bench = _series(list(np.linspace(100, 105, n)))
    strong = _series(list(np.linspace(100, 130, n)))
    weak = _series(list(np.linspace(100, 95, n)))
    assert relative_strength.relative_strength_score(strong, bench) > 50
    assert relative_strength.relative_strength_score(weak, bench) < 50


def test_relative_strength_score_none_on_short_history():
    bench = _series([100.0] * 10)
    stock = _series([100.0] * 10)
    assert relative_strength.relative_strength_score(stock, bench, lookback=63) is None


# ---------------------------------- trend --------------------------------------

def test_market_regime_bull_and_bear():
    n = 260
    bull = _series(list(np.linspace(100, 200, n)))
    bear = _series(list(np.linspace(200, 100, n)))
    label_b, score_b = trend.market_regime(bull)
    label_r, score_r = trend.market_regime(bear)
    assert label_b == "Bullish" and score_b == 80.0
    assert label_r == "Bearish" and score_r == 20.0


def test_market_regime_unknown_on_short_history():
    label, score = trend.market_regime(_series([100.0] * 10))
    assert label == "Unknown" and score == 50.0


def test_sector_strength_score():
    assert trend.sector_strength_score(20.0, 5.0) > 50
    assert trend.sector_strength_score(0.0, 10.0) < 50
    assert trend.sector_strength_score(None, 10.0) is None


# ------------------------------- fundamentals -----------------------------------

def _quarterly_df(row_name: str, values: list[float]) -> pd.DataFrame:
    cols = pd.bdate_range("2023-01-01", periods=len(values), freq="90D")
    return pd.DataFrame([values], index=[row_name], columns=cols)


def test_earnings_acceleration_detects_speedup():
    # oldest -> newest net income: 100, 110 (+10%), 132 (+20%): growth is accelerating
    stmt = _quarterly_df("Net Income", [132.0, 110.0, 100.0])
    accel = fundamentals.earnings_acceleration(stmt, quarters=3)
    assert accel is not None
    assert accel > 0
    assert fundamentals.earnings_acceleration_score(accel) > 50


def test_cash_flow_quality_ratio():
    cfo = _quarterly_df("Operating Cash Flow", [120.0, 110.0])
    ni = _quarterly_df("Net Income", [100.0, 100.0])
    ratio = fundamentals.cash_flow_quality(cfo, ni)
    assert ratio == pytest.approx(1.15)
    assert fundamentals.cash_flow_quality_score(ratio) == 100.0  # saturates above 1.0x


def test_peg_score_monotonic():
    cheap, fair, rich = fundamentals.peg_score(0.5), fundamentals.peg_score(1.0), fundamentals.peg_score(3.0)
    assert cheap > fair > rich
    assert fundamentals.peg_score(None) is None
    assert fundamentals.peg_score(-1) is None


# -------------------------------- merit score ------------------------------------

def test_compute_merit_score_renormalizes_missing_components():
    components = MeritComponents(relative_strength=80.0, earnings_acceleration=80.0)
    score, breakdown = compute_merit_score(components)
    assert score == 80.0  # only two components present, both bullish -> renormalized average is 80
    total_weight = sum(w for _v, w in breakdown.values())
    assert total_weight == pytest.approx(1.0)


def test_compute_merit_score_all_components():
    components = MeritComponents(**{k: 60.0 for k in DEFAULT_WEIGHTS})
    score, _breakdown = compute_merit_score(components)
    assert score == pytest.approx(60.0)


def test_compute_merit_score_none_when_empty():
    score, breakdown = compute_merit_score(MeritComponents())
    assert score is None
    assert breakdown == {}


# --------------------------------- backtest ---------------------------------------

def test_forward_returns_and_summarize_known_values():
    n = 40
    close = _series([100.0] + [100.0] * (n - 1))
    close.iloc[10] = 100.0
    close.iloc[15] = 105.0   # +5% five sessions after a signal at index 10
    close.iloc[20] = 110.0   # +10% ten sessions after
    signal_date = close.index[10]

    outcomes = backtest.forward_returns(close, [signal_date], horizons=(5, 10))
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.entry_price == 100.0
    assert o.forward_returns[5] == pytest.approx(5.0)
    assert o.forward_returns[10] == pytest.approx(10.0)

    summary = backtest.summarize(outcomes, horizons=(5, 10))
    row5 = summary[summary["Horizon (sessions)"] == 5].iloc[0]
    assert row5["Signals"] == 1
    assert row5["Win rate %"] == 100.0


def test_forward_returns_horizon_past_history_is_none():
    close = _series([100.0, 101.0, 102.0])
    outcomes = backtest.forward_returns(close, [close.index[0]], horizons=(30,))
    assert outcomes[0].forward_returns[30] is None


def test_rsi_oversold_dates_detects_crossing():
    rsi = _series([25.0, 28.0, 29.0, 32.0, 40.0, 20.0, 31.0])
    dates = backtest.rsi_oversold_dates(rsi, threshold=30.0)
    assert rsi.index[3] in dates   # 29 -> 32 crosses up through 30
    assert rsi.index[6] in dates   # 20 -> 31 crosses up through 30
    assert rsi.index[0] not in dates


def test_bollinger_breakout_dates_finds_squeeze_then_breakout():
    rng = np.random.default_rng(1)
    calm = 100 + np.cumsum(rng.normal(0, 0.02, 40))
    breakout = np.concatenate([calm, calm[-1] + np.arange(1, 11) * 2.0])
    close = _series(breakout)
    width = volatility.bollinger_width(close)
    dates = backtest.bollinger_breakout_dates(close, width, squeeze_pct=50.0, breakout_lookback=10)
    assert len(dates) > 0
    assert any(d >= close.index[40] for d in dates)  # the constructed breakout itself is detected


# ------------------------------ intraday candidates ---------------------------

from stockmerit.intraday import candidates as intraday  # noqa: E402


def _ohlc_from_close(close: pd.Series, spread: float = 0.01):
    """Synthesize O/H/L/V around a close series for feature/plan tests."""
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = close.shift(1).fillna(close.iloc[0])
    vol = pd.Series([1000.0] * len(close), index=close.index)
    return open_, high, low, vol


def test_intraday_features_basic_shape():
    close = _series(list(np.linspace(100, 130, 80)))  # steady uptrend
    open_, high, low, vol = _ohlc_from_close(close)
    feat = intraday.intraday_features(open_, high, low, close, vol, rsi=65.0, rs_score=70.0)
    assert feat is not None
    for k in ("price", "vwap", "ema20", "ema50", "atr", "atr_pct", "range_pos"):
        assert feat[k] is not None
    assert 0.0 <= feat["range_pos"] <= 1.0
    assert feat["price"] == pytest.approx(float(close.iloc[-1]))


def test_intraday_features_none_on_short_history():
    close = _series([100.0] * 10)
    assert intraday.intraday_features(None, None, None, close, None) is None


def test_uptrend_scores_bullish_downtrend_scores_bearish():
    up = _series(list(np.linspace(100, 140, 80)))
    down = _series(list(np.linspace(140, 100, 80)))
    ou, hu, lu, vu = _ohlc_from_close(up)
    od, hd, ld, vd = _ohlc_from_close(down)
    fu = intraday.intraday_features(ou, hu, lu, up, vu, rsi=68.0, rs_score=72.0)
    fd = intraday.intraday_features(od, hd, ld, down, vd, rsi=32.0, rs_score=28.0)
    su = intraday.score_candidate(fu)
    sd = intraday.score_candidate(fd)
    assert su["bull"] > su["bear"]
    assert sd["bear"] > sd["bull"]
    assert su["bull"] > sd["bull"]


def test_relvol_lifts_both_sides_equally():
    close = _series(list(np.linspace(100, 100.5, 80)))  # nearly flat -> ~neutral direction
    open_, high, low, _ = _ohlc_from_close(close)
    quiet = pd.Series([1000.0] * len(close), index=close.index)
    busy = quiet.copy()
    busy.iloc[-1] = 4000.0
    fq = intraday.intraday_features(open_, high, low, close, quiet, rsi=50.0)
    fb = intraday.intraday_features(open_, high, low, close, busy, rsi=50.0)
    assert fb["rel_vol"] > fq["rel_vol"]
    sq, sb = intraday.score_candidate(fq), intraday.score_candidate(fb)
    assert sb["bull"] > sq["bull"] and sb["bear"] > sq["bear"]


def test_long_plan_levels_are_ordered_and_rr_correct():
    close = _series(list(np.linspace(100, 130, 80)))
    open_, high, low, vol = _ohlc_from_close(close)
    feat = intraday.intraday_features(open_, high, low, close, vol, rsi=65.0)
    plan = intraday.trade_plan(feat, "long")
    assert plan["stop"] < plan["entry_ref"] < plan["target_2r"] < plan["target_3r"]
    r = plan["entry_ref"] - plan["stop"]
    assert plan["target_2r"] == pytest.approx(plan["entry_ref"] + 2 * r, abs=0.02)
    assert plan["target_3r"] == pytest.approx(plan["entry_ref"] + 3 * r, abs=0.02)


def test_short_plan_levels_are_ordered_and_rr_correct():
    close = _series(list(np.linspace(130, 100, 80)))
    open_, high, low, vol = _ohlc_from_close(close)
    feat = intraday.intraday_features(open_, high, low, close, vol, rsi=35.0)
    plan = intraday.trade_plan(feat, "short")
    assert plan["stop"] > plan["entry_ref"] > plan["target_2r"] > plan["target_3r"]
    r = plan["stop"] - plan["entry_ref"]
    assert plan["target_2r"] == pytest.approx(plan["entry_ref"] - 2 * r, abs=0.02)


def test_sector_strength_tilts_scores():
    close = _series(list(np.linspace(100, 130, 80)))
    open_, high, low, vol = _ohlc_from_close(close)
    feat = intraday.intraday_features(open_, high, low, close, vol, rsi=65.0)
    strong = intraday.score_candidate(feat, sector_strength=90.0)
    weak = intraday.score_candidate(feat, sector_strength=10.0)
    assert strong["bull"] > weak["bull"]


def test_rank_splits_and_respects_atr_floor():
    up = _series(list(np.linspace(100, 140, 80)))
    down = _series(list(np.linspace(140, 100, 80)))
    def cand(sym, cl, rsi, rs):
        o, h, l, v = _ohlc_from_close(cl)
        f = intraday.intraday_features(o, h, l, cl, v, rsi=rsi, rs_score=rs)
        return intraday.build_candidate(sym, f, sector="Tech", sector_strength=60.0)
    cands = [cand("UP", up, 68, 72), cand("DOWN", down, 30, 28)]
    out = intraday.rank(cands, top_n=1)
    assert out["longs"][0]["symbol"] == "UP"
    assert out["shorts"][0]["symbol"] == "DOWN"
    # An impossibly high ATR floor drops everything.
    empty = intraday.rank(cands, top_n=5, min_atr_pct=999.0)
    assert empty["longs"] == [] and empty["shorts"] == []
