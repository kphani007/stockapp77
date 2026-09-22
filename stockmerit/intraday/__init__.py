"""Intraday candidate selection: rank a universe into long/short setups and
attach an ATR/structure-based trade plan (entry zone, invalidation, 2R/3R).

Kept framework-agnostic (plain pandas/numpy) so the Streamlit app and any
future API service layer call the same functions without change.
"""
