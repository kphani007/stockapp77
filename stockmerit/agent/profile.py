"""The user's trading profile (spec section 3).

Kept in a plain dict owned by the caller (the Streamlit session), never in a
module global or on disk: the app is multi-user and its filesystem is ephemeral.
"""
from __future__ import annotations

FIELDS = {
    "trading_capital_inr": "Available trading capital in rupees",
    "risk_tolerance": "conservative / moderate / aggressive",
    "investment_horizon": "e.g. intraday, weeks, 1-3 years, 5+ years",
    "trading_experience": "beginner / intermediate / experienced",
    "preferred_style": "long-term, positional, swing, intraday, futures, options buying, options selling",
    "segments_allowed": "which of delivery / intraday / swing / F&O the user wants",
    "max_daily_loss_inr": "Maximum acceptable loss per day in rupees",
    "risk_pct_per_trade": "Percent of capital risked per trade (0.5-1 is the conservative start)",
    "preferred_sectors_or_stocks": "Any preferences or exclusions",
}
NUMERIC = {"trading_capital_inr", "max_daily_loss_inr", "risk_pct_per_trade"}
MAX_TEXT = 300


def clean(fields: dict) -> tuple[dict, list[str]]:
    """Keep only known fields with sane values. Returns (accepted, rejected keys)."""
    ok, bad = {}, []
    for k, v in (fields or {}).items():
        if k not in FIELDS:
            bad.append(str(k)[:40])
        elif k in NUMERIC:
            try:
                f = float(v)
                if f != f or f < 0 or f > 1e12:
                    raise ValueError
                ok[k] = f
            except (TypeError, ValueError):
                bad.append(k)
        elif isinstance(v, (list, tuple)):
            ok[k] = ", ".join(str(x) for x in v)[:MAX_TEXT]
        elif isinstance(v, (str, int, float)) and str(v).strip():
            ok[k] = str(v).strip()[:MAX_TEXT]
        else:
            bad.append(k)
    return ok, bad


def status(store: dict) -> dict:
    return {"profile": dict(store),
            "missing_fields": {k: v for k, v in FIELDS.items() if store.get(k) in (None, "")},
            "note": "Profile lasts for this browser session only."}


def update(store: dict, fields: dict) -> dict:
    ok, bad = clean(fields)
    store.update(ok)
    out = status(store)
    if bad:
        out["rejected_fields"] = bad
        out["valid_fields"] = list(FIELDS)
    return out
