"""Price aggregation, currency normalisation and applied-sticker valuation.

A ``Valuator`` is a snapshot of every stored market quote, converted into the
user's base currency, plus the rules used to turn those quotes into one value
per item.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any

import pandas as pd

from config import Config

from . import db, fees, sanity
from . import settings as app_settings
from .classify import applied_hash_name
from .currency import FxConverter

def soft_cap(raw: float, cap: float) -> float:
    """Diminishing returns: close to ``raw`` while it is small next to ``cap``, and
    approaching - never exceeding - ``cap`` as it grows."""
    if raw <= 0 or cap <= 0:
        return 0.0
    return cap * (1 - math.exp(-raw / cap))


# Days covered by each kind of recent-sales figure ("recent" = Steam's last 24 hours).
PERIOD_DAYS = {"recent": 1, "24 hours": 1, "7 days": 7, "30 days": 30, "90 days": 90}
QUOTE_COLUMNS = ["market_hash_name", "market", "price", "currency", "volume", "url", "is_manual", "fetched_at"]
SINGLE_MARKET_STRATEGIES = app_settings.MARKET_KEYS


class Valuator:
    def __init__(self, fx: FxConverter | None = None, prefs: dict[str, Any] | None = None):
        self.fx = fx or FxConverter.load()
        self.prefs = prefs or app_settings.get_all()
        self.base: str = self.prefs["base_currency"]
        self.strategy: str = self.prefs["valuation_strategy"]
        self.sales = self._load_sales()
        # Listings that can't be right are set aside (see sanity.py): name -> [{market, price, reason, reference}]
        self.set_aside: dict[str, list[dict[str, Any]]] = {}
        self.trusted_sales: dict[str, float] = {}
        self.quotes = self._screen(self._load_quotes())
        self.prices = self._valuation_prices()
        # Applied stickers are valued from the cheapest listing (conservative), not the blend.
        self.lowest = (self.quotes.groupby("market_hash_name")["price_base"].min().to_dict()
                       if not self.quotes.empty else {})
        self.liquidity = self._liquidity()
        self.premium_cap = float(self.prefs.get("sticker_premium_cap", Config.STICKER_PREMIUM_CAP))
        self.fee_overrides = self.prefs.get("selling_fees") or {}
        self._to_eur = self.fx.convert(1.0, self.base, "EUR")

        low = self.fx.convert(Config.STICKER_PRICE_LOW, "USD", self.base) or Config.STICKER_PRICE_LOW
        high = self.fx.convert(Config.STICKER_PRICE_HIGH, "USD", self.base) or Config.STICKER_PRICE_HIGH
        self._sticker_low = max(low, 0.01)
        self._sticker_high = max(high, self._sticker_low * 1.01)
        self._best_net: dict[str, float] | None = None

    # ------------------------------------------------------------------- fees
    def net(self, market: str, price: float | None) -> float | None:
        """What you'd receive after selling at ``price`` on ``market`` and withdrawing it."""
        return fees.net(market, price, self.fee_overrides, self._to_eur)

    def fee_steps(self, market: str, price: float | None) -> dict[str, Any] | None:
        """Selling price -> selling fee -> withdrawal fee -> what you receive, on ``market``."""
        return fees.breakdown(market, price, self.fee_overrides, self._to_eur)

    def cash_out(self, market: str) -> bool:
        return bool(fees.profile(market, self.fee_overrides)["cash_out"])

    # ------------------------------------------------------------- selling speed
    def sell_capacity(self, name: str, market: str) -> int | None:
        """About how many of this item ``market`` sells in BULK_SELL_DAYS, from its recent sales.
        None = no sales figures for that market, so it's assumed to take any number."""
        info = self.sales_by_source.get(name, {}).get(market)
        if info is None:
            # Skinport's sales history covers every item, so no entry means no sales there in 90 days.
            return 0 if market == "skinport" and "skinport" in self.sales_feeds else None
        per_day = (info.get("volume") or 0) / PERIOD_DAYS.get(info.get("period") or "", 1)
        return max(1, round(per_day * Config.BULK_SELL_DAYS)) if per_day > 0 else 0

    def bulk_plan(self, name: str, options: list[dict[str, Any]], quantity: int) -> tuple[float, list[dict[str, Any]]]:
        """Estimated total for selling ``quantity`` copies at once, after fees. Markets are used best
        price first, each taking about what it sells in BULK_SELL_DAYS (listing a penny under its
        cheapest copy); markets without sales figures take the rest. If every market's sales are used
        up, the remainder is counted at the lowest of the prices."""
        remaining, total, plan = int(quantity), 0.0, []
        for option in options:
            if remaining <= 0:
                break
            source = sanity.sticker_inside(name) if option.get("basis") == "sticker_inside" else name
            capacity = self.sell_capacity(source, option["market"])
            take = remaining if capacity is None else min(remaining, capacity)
            if take > 0:
                plan.append({"market": option["market"], "units": take, "net": option["net"],
                             "capacity": capacity, "wallet": option["wallet"]})
                total += take * option["net"]
                remaining -= take
        if remaining > 0 and options:
            last = options[-1]
            plan.append({"market": last["market"], "units": remaining, "net": last["net"], "capacity": None,
                         "wallet": last["wallet"], "overflow": True})
            total += remaining * last["net"]
        return total, plan

    @property
    def best_net(self) -> dict[str, float]:
        """Per item name: the most you'd receive for a plain copy (no stickers) on any market, after fees."""
        if self._best_net is None:
            nets: dict[str, float] = {}
            for name, market, price in zip(self.quotes["market_hash_name"], self.quotes["market"], self.quotes["price_base"]):
                value = self.net(market, float(price))
                if value is not None and value > nets.get(name, 0.0):
                    nets[name] = value
            self._best_net = nets
        return self._best_net

    def sell_options(self, market_quotes: list[dict[str, Any]], extra: float = 0.0,
                     whole_value: float | None = None, markets: list[str] | None = None) -> list[dict[str, Any]]:
        """What you'd receive selling one item on each market, best first.

        Priced from listings: each market's cheapest listing plus ``extra`` (the sticker premium).
        ``whole_value`` (a custom or CSFloat-comparables value) is sold at that price on every
        market in ``markets`` instead. Steam is included; its money is wallet-only (``wallet``)."""
        if whole_value is not None:
            urls = {q["market"]: q.get("url") for q in market_quotes}
            asks = [(m, whole_value, urls.get(m), "value") for m in (markets or [])]
        else:
            asks = [(q["market"], q["price"] + (extra or 0.0), q.get("url"), q.get("basis", "listing"))
                    for q in market_quotes]
        options = []
        for market, price, url, basis in asks:
            steps = self.fee_steps(market, price)
            if steps is None:
                continue
            options.append({"market": market, "url": url, "wallet": not steps["cash_out"], "basis": basis, **steps})
        options.sort(key=lambda o: -o["net"])
        return options

    # ------------------------------------------------------------------ quotes
    def _liquidity(self) -> dict[str, tuple[int, float]]:
        """Per item: (number of markets quoting it, total listings/volume reported)."""
        if self.quotes.empty:
            return {}
        grouped = self.quotes.groupby("market_hash_name")
        markets = grouped["market"].nunique()
        volume = grouped["volume"].apply(lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum())
        return {name: (int(markets[name]), float(volume[name])) for name in markets.index}

    def _load_quotes(self) -> pd.DataFrame:
        df = pd.DataFrame(db.query(f"SELECT {', '.join(QUOTE_COLUMNS)} FROM market_prices"), columns=QUOTE_COLUMNS)
        enabled = set(self.prefs["enabled_markets"])
        df = df[df["market"].isin(enabled) | (df["is_manual"] == 1)].copy()
        df["price_base"] = pd.Series(
            [self.fx.convert(p, c, self.base) for p, c in zip(df["price"], df["currency"])],
            index=df.index,
            dtype="float64",
        )
        # Quotes in a currency we have no rate for cannot be compared, so drop them.
        return df.dropna(subset=["price_base"])

    def _load_sales(self) -> dict[str, dict[str, Any]]:
        """Recent sale prices per item (base currency); the source with the most sales wins.
        Every source's figures are also kept (``sales_by_source``) for selling speeds."""
        out: dict[str, dict[str, Any]] = {}
        self.sales_by_source: dict[str, dict[str, dict[str, Any]]] = {}
        rows = db.query("SELECT * FROM sale_prices")
        self.sales_feeds = {row["source"] for row in rows}
        for row in rows:
            price = self.fx.convert(row["price"], row["currency"], self.base)
            if not price or price <= 0:
                continue
            info = {"price": price, "volume": row["volume"], "source": row["source"],
                    "period": row["period"], "fetched_at": row["fetched_at"]}
            self.sales_by_source.setdefault(row["market_hash_name"], {})[row["source"]] = info
            current = out.get(row["market_hash_name"])
            # Real sales beat an estimate (CSFloat's reference price has no volume); more sales win.
            if current is None or (row["volume"] or -1) > (current["volume"] or -1):
                out[row["market_hash_name"]] = info
        return out

    def _screen(self, df: pd.DataFrame) -> pd.DataFrame:
        """Set aside listings far above what the item really sells for (see sanity.py)."""
        listed = set(df["market_hash_name"]) if not df.empty else set()
        for name, info in self.sales.items():   # items with sales but no listing at all
            if name not in listed:
                self.trusted_sales[name] = info["price"]
        if df.empty:
            return df
        usd = self.fx.convert(1.0, "USD", self.base) or 1.0
        drop: list[Any] = []
        reference: dict[str, float] = {}   # what kept evidence says each item is worth (for slabs)
        groups = dict(tuple(df.groupby("market_hash_name")))
        for name in sorted(groups, key=lambda n: n.startswith(sanity.SLAB_PREFIX)):  # stickers before slabs
            group = groups[name]
            listings = {m: float(p) for m, p in zip(group["market"], group["price_base"])}
            inner_name = sanity.sticker_inside(name)
            inner = reference.get(inner_name) if inner_name else None
            sold = self.sales.get(name, {}).get("price")
            kept, aside, trusted = sanity.screen(listings, sold, inner, usd)
            if trusted:
                self.trusted_sales[name] = trusted
            reference[name] = trusted or (statistics.median(kept.values()) if kept else None)
            for market, verdict in aside.items():
                drop += list(group.index[group["market"] == market])
                self.set_aside.setdefault(name, []).append({"market": market, "price": listings[market], **verdict})
        return df.drop(index=drop)

    def _valuation_prices(self) -> dict[str, float]:
        df = self.quotes
        if df.empty:
            return {}
        grouped = df.groupby("market_hash_name")["price_base"]
        if self.strategy == "lowest":
            series = grouped.min()
        elif self.strategy == "highest":
            series = grouped.max()
        elif self.strategy == "mean":
            series = grouped.mean()
        elif self.strategy in SINGLE_MARKET_STRATEGIES:
            preferred = df[df["market"] == self.strategy].groupby("market_hash_name")["price_base"].min()
            series = preferred.combine_first(grouped.median())  # fall back when that market has no quote
        else:
            series = grouped.median()
        return {name: float(value) for name, value in series.items()}

    def price(self, market_hash_name: str) -> float | None:
        """Market value: the valuation strategy over usable listings, else the fallback below."""
        found = self.prices.get(market_hash_name)
        if found is None:
            fallback = self.fallback_quotes(market_hash_name)
            if fallback:
                found = statistics.median(q["price"] for q in fallback)
        return found

    def fallback_quotes(self, market_hash_name: str) -> list[dict[str, Any]]:
        """For an item with no usable listing: its recent sales (on the market they're from), or for
        a Sticker Slab, the sticker inside (you can crack the slab open and sell the sticker)."""
        if market_hash_name in self.prices:
            return []
        sold = self.trusted_sales.get(market_hash_name)
        if sold:
            info = self.sales.get(market_hash_name, {})
            market = (info.get("source") or "skinport").removesuffix("_ref")   # csfloat_ref -> sold on CSFloat
            return [{"market": market, "price": sold, "url": None, "basis": "sales",
                     "net": self.net(market, sold), "cash_out": self.cash_out(market)}]
        inner = sanity.sticker_inside(market_hash_name)
        if inner and not self.quotes.empty:
            rows = self.quotes[self.quotes["market_hash_name"] == inner].sort_values("price_base")
            return [{"market": r.market, "price": float(r.price_base), "url": r.url, "basis": "sticker_inside",
                     "net": self.net(r.market, float(r.price_base)), "cash_out": self.cash_out(r.market)}
                    for r in rows.itertuples()]
        return []

    def best_deals(self) -> dict[str, dict[str, Any]]:
        """Cheapest current quote per item, after currency normalisation."""
        df = self.quotes
        if df.empty:
            return {}
        cheapest = df.loc[df.groupby("market_hash_name")["price_base"].idxmin()]
        return {
            row.market_hash_name: {"market": row.market, "price": float(row.price_base), "url": row.url}
            for row in cheapest.itertuples()
        }

    # ---------------------------------------------------------------- stickers
    def sticker_rate(self, loose_price: float) -> float:
        """Share of a sticker's loose price that it adds to the skin it is applied to.

        Cheap stickers add the minimum rate (default 5 %), expensive ones the maximum
        (default 15 %), with log-scale interpolation in between. This mirrors how the
        market rarely pays more than a fraction of a sticker's loose price.
        """
        rate_min = float(self.prefs["sticker_rate_min"])
        rate_max = float(self.prefs["sticker_rate_max"])
        if loose_price <= self._sticker_low:
            return rate_min
        if loose_price >= self._sticker_high:
            return rate_max
        t = (math.log10(loose_price) - math.log10(self._sticker_low)) / (
            math.log10(self._sticker_high) - math.log10(self._sticker_low)
        )
        return rate_min + t * (rate_max - rate_min)

    def applied_premium(
        self, applied: list[dict[str, Any]], base_price: float | None
    ) -> tuple[float, list[dict[str, Any]], dict[str, Any]]:
        """Premium the stickers/patches/charms add to one item, with a breakdown.

        1. Each sticker's loose price is its *cheapest* current listing.
        2. It contributes a share of that price (``sticker_rate``), more for 4-of-a-kind
           crafts and less when scraped. This is the "raw" premium.
        3. The total is capped relative to the skin's own price (default: it can at most
           double the skin's value; 3x that for crafts), approaching the cap gradually.
           Rare stickers on cheap skins rarely sell for anything like their loose value.
        """
        counts = Counter((a["kind"], a["name"]) for a in applied)
        raw = loose_total = 0.0
        breakdown = []
        for entry in applied:
            hash_name = applied_hash_name(entry["kind"], entry["name"])
            loose = self.lowest.get(hash_name)
            markets, volume = self.liquidity.get(hash_name, (0, 0.0))
            scrape = float(entry.get("scrape") or 0)
            detail = {
                "kind": entry["kind"],
                "name": entry["name"],
                "slot": entry.get("slot", 0),
                "scrape": scrape,
                "market_hash_name": hash_name,
                "loose_price": loose,
                "rate": None,
                "contribution": 0.0,
                "counted": 0.0,
                "craft": False,
                "thin": bool(loose) and markets <= 1 and volume <= 3,  # one market, a handful of listings
            }
            if loose:
                rate = self.sticker_rate(loose)
                if entry["kind"] == "sticker" and counts[(entry["kind"], entry["name"])] >= 4:
                    rate *= Config.STICKER_CRAFT_BONUS  # matching 4x "craft"
                    detail["craft"] = True
                rate *= 1 - scrape  # scraped stickers are worth far less
                detail["rate"] = rate
                detail["contribution"] = loose * rate
                raw += detail["contribution"]
                loose_total += loose
            breakdown.append(detail)

        craft = any(d["craft"] for d in breakdown)
        summary = {"loose_total": loose_total, "raw": raw, "cap": None, "capped": False, "no_base": False}
        if raw <= 0:
            return 0.0, breakdown, summary
        if not base_price or base_price <= 0:
            summary["no_base"] = True  # can't judge a premium without the skin's own price
            return 0.0, breakdown, summary
        cap = base_price * self.premium_cap * (Config.STICKER_CRAFT_CAP_MULTIPLIER if craft else 1)
        counted = soft_cap(raw, cap)
        summary.update(cap=cap, capped=counted < raw * 0.98)
        for detail in breakdown:
            detail["counted"] = detail["contribution"] * (counted / raw)
        return counted, breakdown, summary

    # ------------------------------------------------------------------- items
    def custom_price(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None:
            return None
        currency = currency if isinstance(currency, str) and currency else self.base
        return self.fx.convert(amount, currency, self.base)
