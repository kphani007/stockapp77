"""Position sizing and transaction-cost estimates (spec section 9)."""
from __future__ import annotations

import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config"
HARD_MAX_RISK_PCT = 2.0     # the sizing tool refuses anything above this per trade
DEFAULT_RISK_PCT = 0.5


def load_charges() -> dict:
    return json.loads((CONFIG / "charges.json").read_text())


def estimate_charges(segment: str, buy_price: float, sell_price: float, quantity: float) -> dict:
    """Round-trip cost estimate. For futures/options, quantity = lots x lot size and
    prices are per unit (premium for options)."""
    cfg = load_charges()
    seg = cfg["segments"].get(segment)
    if seg is None:
        return {"error": f"unknown segment '{segment}'", "valid": list(cfg["segments"])}
    buy_val, sell_val = buy_price * quantity, sell_price * quantity
    turnover = buy_val + sell_val

    def brokerage(v):
        if "brokerage_flat_per_order" in seg:
            return seg["brokerage_flat_per_order"]
        b = v * seg["brokerage_pct"] / 100
        cap = seg.get("brokerage_cap_per_order", 0)
        return min(b, cap) if cap else b

    brk = brokerage(buy_val) + brokerage(sell_val)
    stt = buy_val * seg["stt_buy_pct"] / 100 + sell_val * seg["stt_sell_pct"] / 100
    txn = turnover * seg["exchange_txn_pct"] / 100
    sebi = turnover * cfg["sebi_turnover_pct"] / 100
    stamp = buy_val * seg["stamp_buy_pct"] / 100
    gst = (brk + txn + sebi) * cfg["gst_pct"] / 100
    dp = seg.get("dp_charge_per_sell", 0)
    total = brk + stt + txn + sebi + stamp + gst + dp
    gross = sell_val - buy_val
    return {
        "segment": segment, "quantity": quantity,
        "buy_value": round(buy_val, 2), "sell_value": round(sell_val, 2),
        "brokerage": round(brk, 2), "stt": round(stt, 2), "exchange_txn": round(txn, 2),
        "sebi": round(sebi, 2), "stamp_duty": round(stamp, 2), "gst": round(gst, 2),
        "dp_charges": round(dp, 2),
        "total_charges": round(total, 2),
        "charges_per_unit": round(total / quantity, 4) if quantity else None,
        "gross_pnl": round(gross, 2), "net_pnl": round(gross - total, 2),
        "slippage": "NOT included - add your own estimate",
        "rates_last_reviewed": cfg["last_reviewed"],
        "caveat": "Estimate from config/charges.json; verify against your broker's contract note.",
    }


def position_size(capital: float, entry: float, stop: float, risk_pct: float = DEFAULT_RISK_PCT,
                  lot_size: int | None = None, max_daily_loss: float | None = None,
                  loss_today: float = 0.0, leverage: float = 1.0) -> dict:
    """Quantity = max rupee risk / |entry - stop|  (spec formula).

    lot_size: pass for futures/options (prices per unit). The lot size must come from a
    verified current source; it is never assumed here.
    """
    warnings: list[str] = []
    if capital <= 0 or entry <= 0 or stop <= 0:
        return {"error": "capital, entry and stop must be positive"}
    if risk_pct > HARD_MAX_RISK_PCT:
        return {"error": f"risk_pct {risk_pct}% exceeds the hard cap of {HARD_MAX_RISK_PCT}% "
                         "per trade. Reduce risk; this tool will not size it."}
    if risk_pct > 1.0:
        warnings.append("Risk per trade above 1% of capital is aggressive.")
    per_unit = abs(entry - stop)
    if per_unit == 0:
        return {"error": "entry and stop are equal; risk per unit is zero"}
    risk_rupees = capital * risk_pct / 100
    if max_daily_loss is not None:
        room = max_daily_loss - abs(loss_today)
        if room <= 0:
            return {"quantity": 0, "decision": "NO TRADE",
                    "reason": "Maximum daily loss already reached. Stop trading for today."}
        if room < risk_rupees:
            warnings.append(f"Risk cut from {risk_rupees:.0f} to {room:.0f} to stay inside the daily loss limit.")
            risk_rupees = room
    raw = risk_rupees / per_unit
    if lot_size:
        lots = math.floor(raw / lot_size)
        qty = lots * lot_size
    else:
        lots = None
        qty = math.floor(raw)
        affordable = math.floor(capital * leverage / entry)
        if qty > affordable:
            warnings.append(f"Quantity capped from {qty} to {affordable} by available capital "
                            f"(leverage {leverage}x).")
            qty = affordable
    out = {
        "quantity": qty, "lots": lots, "lot_size": lot_size,
        "risk_per_unit": round(per_unit, 2),
        "max_rupee_risk_allowed": round(risk_rupees, 2),
        "actual_rupee_risk_at_stop": round(qty * per_unit, 2),
        "risk_pct_of_capital": round(qty * per_unit / capital * 100, 3),
        "position_value": round(qty * entry, 2),
        "stop_distance_pct": round(per_unit / entry * 100, 2),
        "warnings": warnings,
        "caveats": ["Gap moves and slippage can make the realised loss larger than the stop implies.",
                    "For futures and short options, exposure and margin are far larger than the risk "
                    "figure; check the broker margin calculator."],
    }
    if qty == 0:
        out["decision"] = "NO TRADE"
        out["reason"] = ("Risk budget is too small for one lot/share at this stop distance. "
                         "Do not widen the budget or tighten the stop artificially to make it fit.")
    return out
