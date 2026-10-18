"""Intraday position sizing: turn a max-loss budget and a stop distance into a
share count, a target and net-of-charges outcomes.

Pure Python (no pandas, no Streamlit) so the Streamlit app and any future API
layer can call the same function.

This is arithmetic, not a forecast. It does not say a trade is a good idea.
Charges are whatever flat estimate the caller passes in; take the real figure
from your broker's charge calculator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TICK = 0.05  # NSE equity tick size, in rupees


@dataclass
class SizingResult:
    ok: bool
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    direction: str = "buy"
    shares: int = 0
    capped_by_capital: bool = False
    entry: float = 0.0
    stop: float = 0.0
    risk_per_share: float = 0.0
    position_value: float = 0.0
    loss_at_stop: float = 0.0
    target: float = 0.0
    gross_profit: float = 0.0
    charges: float = 0.0
    net_profit: float = 0.0
    net_loss: float = 0.0
    net_reward_to_risk: float = 0.0
    loss_pct_of_capital: float = 0.0
    charges_pct_of_profit: float = 0.0


def _fail(msg: str, direction: str = "buy") -> SizingResult:
    return SizingResult(ok=False, error=msg, direction=direction)


def size_position(
    direction: str,
    entry: float,
    stop: float,
    max_loss: float,
    capital: float,
    reward_multiple: float = 2.0,
    charges: float = 0.0,
) -> SizingResult:
    """Size an intraday trade so a stop-out costs at most ``max_loss``.

    direction        "buy" (long) or "sell" (sell first / short)
    entry, stop      prices in rupees; the stop must be on the losing side
    max_loss         the most you accept losing on this trade, before charges
    capital          the cap on position value (entry x shares <= capital)
    reward_multiple  target distance as a multiple of the stop distance
    charges          flat round-trip charges estimate in rupees
    """
    direction = str(direction).lower()
    if direction not in ("buy", "sell"):
        return _fail("Direction must be 'buy' or 'sell'.")

    for name, val in (("entry", entry), ("stop", stop), ("max_loss", max_loss), ("capital", capital)):
        if not isinstance(val, (int, float)) or val != val or val <= 0:
            return _fail("Enter entry, stop-loss, max loss and capital to see the size.", direction)

    if direction == "buy" and stop >= entry:
        return _fail("For a buy, the stop-loss must be below the entry price.", direction)
    if direction == "sell" and stop <= entry:
        return _fail("For a sell-first trade, the stop-loss must be above the entry price.", direction)

    if not isinstance(reward_multiple, (int, float)) or reward_multiple != reward_multiple or reward_multiple <= 0:
        reward_multiple = 2.0
    if not isinstance(charges, (int, float)) or charges != charges or charges < 0:
        charges = 0.0

    rps = abs(entry - stop)
    by_risk = int(max_loss // rps)
    by_capital = int(capital // entry)
    shares = min(by_risk, by_capital)
    if shares < 1:
        return _fail("Your max loss is too small for this stop distance. "
                     "Use a tighter stop or raise the max loss.", direction)

    raw_target = entry + reward_multiple * rps if direction == "buy" else entry - reward_multiple * rps
    target = round(round(raw_target / TICK) * TICK, 2)
    gross = shares * abs(target - entry)
    loss = shares * rps
    net_profit = gross - charges
    net_loss = loss + charges

    res = SizingResult(
        ok=True,
        direction=direction,
        shares=shares,
        capped_by_capital=by_capital < by_risk,
        entry=entry,
        stop=stop,
        risk_per_share=rps,
        position_value=shares * entry,
        loss_at_stop=loss,
        target=target,
        gross_profit=gross,
        charges=charges,
        net_profit=net_profit,
        net_loss=net_loss,
        net_reward_to_risk=(net_profit / net_loss) if net_loss > 0 else 0.0,
        loss_pct_of_capital=loss / capital * 100,
        charges_pct_of_profit=(charges / gross * 100) if gross > 0 else 0.0,
    )

    if res.capped_by_capital:
        res.notes.append(f"Capital limits this trade to {by_capital:,} shares, "
                         "so your loss at the stop is below your max loss.")
    if max_loss > capital * 0.01:
        res.notes.append("Your max loss is more than 1% of capital. Smaller risk per trade "
                         "helps you survive a losing streak.")
    if rps / entry < 0.003:
        res.notes.append("This stop is very tight. Normal price noise may hit it.")
    if res.charges_pct_of_profit > 10:
        res.notes.append(f"Charges take about {round(res.charges_pct_of_profit)}% of your target profit.")
    return res
