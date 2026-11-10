"""Option strategy payoff at expiry and Black-Scholes Greeks.

There is NO option-chain feed in the default setup: premiums, IV, OI and lot size must
come from the user, a broker API or a verified web source. These functions only do the
arithmetic on numbers they are given.
"""
from __future__ import annotations

import math


def _leg_payoff(leg: dict, s: float) -> float:
    sign = 1 if leg["side"].upper() == "BUY" else -1
    t = leg["type"].upper()
    if t == "CE":
        intrinsic = max(s - leg["strike"], 0.0)
    elif t == "PE":
        intrinsic = max(leg["strike"] - s, 0.0)
    elif t == "FUT":
        return sign * (s - leg["price"]) * leg.get("ratio", 1)
    else:
        raise ValueError(f"leg type must be CE, PE or FUT, got {t}")
    return sign * (intrinsic - leg["premium"]) * leg.get("ratio", 1)


def strategy_payoff(legs: list[dict], lot_size: int, lots: int = 1) -> dict:
    """legs: [{type: CE|PE|FUT, side: BUY|SELL, strike, premium, ratio?}] (FUT uses price).
    All numbers per unit; result is scaled by lot_size x lots."""
    if not legs:
        return {"error": "no legs supplied"}
    for leg in legs:
        t = leg.get("type", "").upper()
        need = ("price",) if t == "FUT" else ("strike", "premium")
        if any(k not in leg for k in need) or "side" not in leg:
            return {"error": f"leg missing fields {need + ('side',)}: {leg}"}
    mult = lot_size * lots
    anchors = [l["strike"] if l["type"].upper() != "FUT" else l["price"] for l in legs]
    nodes = sorted({0.0, *anchors, max(anchors) * 2})
    total = lambda s: sum(_leg_payoff(l, s) for l in legs)  # noqa: E731
    vals = [total(s) for s in nodes]

    def net(kind_sign):  # net units of exposure in the tails
        return sum((1 if l["side"].upper() == "BUY" else -1) * l.get("ratio", 1)
                   for l in legs if l["type"].upper() in kind_sign)

    up_slope = net(("CE", "FUT"))
    unlimited_loss = up_slope < 0
    unlimited_profit = up_slope > 0
    max_profit = None if unlimited_profit else max(vals)
    max_loss = None if unlimited_loss else min(vals)

    breakevens = []
    for (s0, v0), (s1, v1) in zip(zip(nodes, vals), zip(nodes[1:], vals[1:])):
        if v0 == 0:
            breakevens.append(s0)
        elif v0 * v1 < 0:
            breakevens.append(s0 + (s1 - s0) * (-v0) / (v1 - v0))
    if up_slope != 0 and vals[-1] * up_slope < 0:  # crosses zero beyond the last node
        breakevens.append(nodes[-1] - vals[-1] / up_slope)

    short_calls = -sum((1 if l["side"].upper() == "BUY" else -1) * l.get("ratio", 1)
                       for l in legs if l["type"].upper() == "CE")
    short_puts = -sum((1 if l["side"].upper() == "BUY" else -1) * l.get("ratio", 1)
                      for l in legs if l["type"].upper() == "PE")
    has_fut = any(l["type"].upper() == "FUT" for l in legs)
    naked = unlimited_loss or (short_puts > 0 and not has_fut) or (short_calls > 0 and not has_fut)
    net_credit = sum((-1 if l["side"].upper() == "BUY" else 1) * l.get("premium", 0) * l.get("ratio", 1)
                     for l in legs)
    out = {
        "net_premium_per_unit": round(net_credit, 2),
        "net_premium_type": "credit" if net_credit > 0 else "debit" if net_credit < 0 else "zero",
        "max_profit": None if max_profit is None else round(max_profit * mult, 2),
        "max_profit_unlimited": unlimited_profit,
        "max_loss": None if max_loss is None else round(max_loss * mult, 2),
        "max_loss_unlimited": unlimited_loss,
        "breakevens": sorted({round(b, 2) for b in breakevens}),
        "reward_to_risk": (round(max_profit / -max_loss, 2)
                           if max_profit is not None and max_loss is not None and max_loss < 0 else None),
        "lot_size": lot_size, "lots": lots,
        "defined_risk": not naked,
        "margin": "NOT computed (SPAN + exposure). Check your broker's margin calculator.",
        "caveats": ["Payoff is at expiry only, before charges and slippage.",
                    "Lot size must be verified against the current NSE contract specification.",
                    "Stock options are physically settled if held to expiry in the money."],
    }
    if naked:
        out["warning"] = ("UNCOVERED SHORT OPTION EXPOSURE: this structure has a short option not "
                          "fully hedged by a long option. Spec rule: avoid naked option selling. "
                          "Add the protective leg or do not recommend it.")
    return out


def _n(x):  # standard normal CDF
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_greeks(spot: float, strike: float, days_to_expiry: float, iv_pct: float,
              option_type: str = "CE", rate_pct: float = 6.5) -> dict:
    """European Black-Scholes value and Greeks (no dividends). iv_pct and rate_pct are
    annualised percentages supplied by the caller; rate_pct default is a placeholder."""
    if min(spot, strike, days_to_expiry, iv_pct) <= 0:
        return {"error": "spot, strike, days_to_expiry and iv_pct must be positive"}
    t, v, r = days_to_expiry / 365, iv_pct / 100, rate_pct / 100
    d1 = (math.log(spot / strike) + (r + v * v / 2) * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)
    call = option_type.upper() == "CE"
    disc = math.exp(-r * t)
    price = spot * _n(d1) - strike * disc * _n(d2) if call else strike * disc * _n(-d2) - spot * _n(-d1)
    theta = (-spot * _pdf(d1) * v / (2 * math.sqrt(t))
             - (r * strike * disc * _n(d2) if call else -r * strike * disc * _n(-d2)))
    return {
        "model_price": round(price, 2),
        "delta": round(_n(d1) if call else _n(d1) - 1, 4),
        "gamma": round(_pdf(d1) / (spot * v * math.sqrt(t)), 6),
        "theta_per_day": round(theta / 365, 2),
        "vega_per_1pct_iv": round(spot * _pdf(d1) * math.sqrt(t) / 100, 2),
        "inputs": {"spot": spot, "strike": strike, "days_to_expiry": days_to_expiry,
                   "iv_pct": iv_pct, "rate_pct": rate_pct, "type": option_type.upper()},
        "caveat": "Theoretical model output from the IV you supplied; not a market quote.",
    }
