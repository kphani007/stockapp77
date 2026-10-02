import pandas as pd
from stockmerit.radar import state, fund_score

def test_radar_state_thresholds():
    assert state(75) == "Core Candidate"
    assert state(65) == "Emerging"
    assert state(55) == "Watch"
    assert state(54.9) == "Weak"

def test_fund_score_positive_growth():
    score = fund_score({
        "revenueGrowth": .20,
        "earningsGrowth": .30,
        "returnOnEquity": .20,
        "debtToEquity": 20,
        "freeCashflow": 100,
        "operatingCashflow": 120,
        "pegRatio": 1.0,
    })
    assert score is not None
    assert 0 <= score <= 100
