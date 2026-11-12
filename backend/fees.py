"""Selling and cash-out fees: what you'd actually receive for an item on each market.

Defaults were taken from public fee information in November 2026. Marketplaces change
their fees, so every value can be overridden in Settings ("selling_fees").

Models:
  * "of_price": the market keeps a share of the sale price (most marketplaces).
  * "on_top":   the fee is added on top of what the seller receives (Steam: the buyer
                pays your amount + 5 % Steam fee + 10 % CS2 fee, so a £100 sale nets ~£86.96).
Then the withdrawal (cash-out) fee comes off what's left. Steam Wallet money can't be
withdrawn at all, so Steam is marked cash_out = False and never counts as cash.
"""
from __future__ import annotations

from typing import Any

FEES_CHECKED = "November 2026"

DEFAULTS: dict[str, dict[str, Any]] = {
    "steam": {"mode": "on_top", "sale": 0.15, "withdraw": 0.0, "cash_out": False,
              "note": "5% Steam fee + 10% CS2 fee, added on top of what you receive (minimum 1p each). "
                      "The money goes to your Steam Wallet and can't be withdrawn."},
    "skinport": {"sale": 0.08, "sale_high": 0.06, "high_eur": 1000.0, "withdraw": 0.0, "cash_out": True,
                 "note": "8% sale fee (6% on items worth €1,000 or more). No payout fee, though your bank may charge."},
    "csfloat": {"sale": 0.02, "withdraw": 0.025, "cash_out": True,
                "note": "2% sale fee. Withdrawals cost 2.5% for new sellers, falling to 0.5% as your lifetime sales grow."},
    "marketcsgo": {"sale": 0.05, "withdraw": 0.03, "cash_out": True,
                   "note": "5% sale fee. The withdrawal fee depends on the payout method (roughly 1-5%)."},
    "waxpeer": {"sale": 0.06, "withdraw": 0.02, "cash_out": True,
                "note": "6% sale fee for new sellers (lower at higher volume). About 2% on crypto withdrawals."},
}
# Manual-quote markets, matched by their name (lower-case, spaces removed).
MANUAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "buff163": {"sale": 0.025, "withdraw": 0.01, "cash_out": False,
                "note": "2.5% sale fee. Cash withdrawals are only available to verified Chinese residents."},
}
GENERIC = {"sale": 0.0, "withdraw": 0.0, "cash_out": True, "note": "No fees set - add them in Settings if this market charges any."}


def _manual_name(market: str) -> str:
    return market.split(":", 1)[1].lower().replace(" ", "") if market.startswith("manual:") else ""


def profile(market: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """The fee profile for a market: defaults, then anything you changed in Settings."""
    base = DEFAULTS.get(market) or MANUAL_DEFAULTS.get(_manual_name(market)) or GENERIC
    result = {"mode": "of_price", **base}
    custom = (overrides or {}).get(market) or {}
    for key in ("sale", "withdraw", "cash_out"):
        if key in custom:
            result[key] = custom[key]
    if "sale" in custom:
        result.pop("sale_high", None)  # a hand-set sale fee replaces the tiered default
    return result


def net(market: str, price: float | None, overrides: dict[str, Any] | None = None,
        to_eur: float | None = None) -> float | None:
    """What you'd receive after selling at ``price`` on ``market`` and withdrawing the money.
    ``to_eur`` converts 1 unit of the price's currency to EUR (for Skinport's €1,000 tier)."""
    if price is None or price <= 0:
        return None
    p = profile(market, overrides)
    if p["mode"] == "on_top":
        received = price / (1 + p["sale"])
        received = min(received, price - 0.02)  # each of Steam's two fees is at least 1p/1c
    else:
        rate = p["sale"]
        if "sale_high" in p and to_eur and price * to_eur >= p["high_eur"]:
            rate = p["sale_high"]
        received = price * (1 - rate)
    return max(received * (1 - p["withdraw"]), 0.0)


def validate(raw: Any) -> dict[str, dict[str, Any]]:
    """Clean the "selling_fees" setting: {market: {sale, withdraw, cash_out}}."""
    if not isinstance(raw, dict):
        raise ValueError("Selling fees must be an object keyed by market")
    clean: dict[str, dict[str, Any]] = {}
    for market, values in raw.items():
        if not isinstance(market, str) or not market or len(market) > 60 or not isinstance(values, dict):
            raise ValueError("Invalid selling-fee entry")
        entry: dict[str, Any] = {}
        for key in ("sale", "withdraw"):
            if key in values:
                try:
                    rate = float(values[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{market}: fees must be numbers") from None
                if rate > 1:
                    rate /= 100  # accept 8 meaning 8 %
                if not 0 <= rate <= 0.5:
                    raise ValueError(f"{market}: fees must be between 0% and 50%")
                entry[key] = round(rate, 5)
        if "cash_out" in values:
            entry["cash_out"] = bool(values["cash_out"])
        clean[market] = entry
    return clean
