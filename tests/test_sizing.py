import math

from stockmerit.intraday.sizing import size_position


def test_buy_example_matches_hand_math():
    r = size_position("buy", entry=500, stop=495, max_loss=2500, capital=400000,
                      reward_multiple=2, charges=300)
    assert r.ok
    assert r.shares == 500
    assert r.position_value == 250000
    assert r.loss_at_stop == 2500
    assert r.target == 510.0
    assert r.gross_profit == 5000
    assert r.net_profit == 4700
    assert r.net_loss == 2800
    assert math.isclose(r.net_reward_to_risk, 4700 / 2800)
    assert math.isclose(r.loss_pct_of_capital, 0.625)


def test_sell_first_target_is_below_entry():
    r = size_position("sell", entry=500, stop=505, max_loss=2500, capital=400000)
    assert r.ok
    assert r.shares == 500
    assert r.target == 490.0


def test_stop_on_wrong_side_is_rejected():
    assert not size_position("buy", 500, 505, 2500, 400000).ok
    assert not size_position("sell", 500, 495, 2500, 400000).ok


def test_capital_caps_the_share_count():
    r = size_position("buy", entry=2000, stop=1999, max_loss=5000, capital=400000)
    assert r.ok
    assert r.shares == 200
    assert r.capped_by_capital
    assert r.loss_at_stop == 200  # below the 5,000 budget
    assert r.position_value <= 400000


def test_loss_never_exceeds_budget():
    for stop in (499.0, 497.5, 495.0, 490.0):
        r = size_position("buy", 500, stop, 2500, 10_000_000)
        assert r.ok
        assert r.loss_at_stop <= 2500


def test_too_small_budget_returns_error():
    r = size_position("buy", entry=500, stop=400, max_loss=50, capital=400000)
    assert not r.ok
    assert "too small" in r.error


def test_bad_inputs_do_not_raise():
    assert not size_position("buy", float("nan"), 495, 2500, 400000).ok
    assert not size_position("buy", 500, 495, 0, 400000).ok
    assert not size_position("hold", 500, 495, 2500, 400000).ok
    r = size_position("buy", 500, 495, 2500, 400000, reward_multiple=-1, charges=-5)
    assert r.ok and r.target == 510.0 and r.charges == 0.0


def test_target_rounds_to_tick():
    r = size_position("buy", entry=277.20, stop=273.70, max_loss=2500, capital=400000)
    assert r.ok
    assert abs(r.target / 0.05 - round(r.target / 0.05)) < 1e-6
    assert r.target == 284.20  # 277.20 + 2 x 3.50
