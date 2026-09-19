"""Pure, framework-agnostic signal features computed from OHLCV and
fundamental data. Every score function here returns 0-100 (50 = neutral or
unknown-average) so scores can be blended in stockmerit.scoring.merit_score.

These are heuristic mappings, not empirically fitted models -- use
stockmerit.scoring.backtest to check whether a feature actually has
predictive value before weighting it heavily.
"""
