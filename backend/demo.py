"""Synthetic demo data (``python app.py --demo``).

Everything created here is generated: the inventory belongs to nobody, and the
prices, exchange rates and history are random walks - NOT real market data.
It is stored in a separate database file (data/demo.db) so it never mixes with
your real portfolio.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from config import Config

from . import db, settings
from .classify import applied_hash_name, categorize, exterior_from_name
from .valuation import soft_cap

DEMO_OWNER = "DEMO"  # not a SteamID - demo items can never be synced
HISTORY_DAYS = 420
SOURCE_NOTE = "Demo data (synthetic)"

# Approximate USD rates used only to make the synthetic numbers look plausible.
DEMO_FX = {"USD": 1.0, "EUR": 0.90, "GBP": 0.76, "CNY": 6.70, "CAD": 1.42, "AUD": 1.47, "JPY": 158.0,
           "PLN": 3.98, "SEK": 9.95, "CHF": 0.83, "NOK": 9.5, "DKK": 6.75, "CZK": 22.0, "BRL": 5.6}

# (source, market_hash_name, quantity, synthetic USD price or None, days held, extras)
DEMO_ITEMS = [
    ("steam", "AK-47 | Redline (Field-Tested)", 1, 18.40, 400,
     {"float_value": 0.2213, "paint_seed": 661, "stickers": [("sticker", "Crown (Foil)")] * 4}),
    ("steam", "AWP | Asiimov (Battle-Scarred)", 1, 92.00, 380, {"float_value": 0.6120, "paint_seed": 37}),
    ("steam", "★ Karambit | Doppler (Factory New)", 1, 860.00, 300, {"float_value": 0.0121, "paint_seed": 412}),
    ("steam", "M4A1-S | Printstream (Minimal Wear)", 1, 138.00, 260,
     {"float_value": 0.0874, "paint_seed": 902,
      "stickers": [("sticker", "Vitality (Holo) | Paris 2023"), ("sticker", "FaZe Clan (Holo) | Paris 2023")]}),
    ("steam", "Glock-18 | Water Elemental (Minimal Wear)", 1, 6.10, 410, {"float_value": 0.1012}),
    ("steam", "Desert Eagle | Blaze (Factory New)", 1, 470.00, 150, {"float_value": 0.0388, "paint_seed": 123}),
    ("steam", "USP-S | Kill Confirmed (Field-Tested)", 1, 54.00, 220,
     {"float_value": 0.1873, "stickers": [("sticker", "Crown (Foil)")]}),
    ("steam", "Sticker | Crown (Foil)", 2, 145.00, 330, {}),
    ("steam", "Sticker | Vitality (Holo) | Paris 2023", 3, 4.20, 200, {}),
    ("steam", "Sticker Slab | IEM (Gold) | Rio 2022", 1, 1.10, 90, {}),
    ("steam", "Recoil Case", 12, 0.29, 360, {}),
    ("steam", "Kilowatt Case", 5, 0.80, 240, {}),
    ("steam", "Paris 2023 Mirage Souvenir Package", 2, 3.10, 180, {}),
    ("steam", "Storage Unit", 1, None, 400, {"name_tag": "Long Holds", "marketable": 0, "is_storage": True}),
    ("manual", "★ Butterfly Knife | Fade (Factory New)", 1, 2350.00, 120,
     {"float_value": 0.0093, "paint_seed": 551, "custom_price": 2600.0, "purchase_price": 2150.0,
      "notes": "Demo: custom price used for a high-fade pattern."}),
    ("manual", "Storage Unit", 1, None, 300, {"name_tag": "Case Stash", "is_storage": True}),
]
# Contents of the containers above: (container name tag, market_hash_name, quantity, USD price, days held)
DEMO_CONTENTS = [
    ("Long Holds", "Dreams & Nightmares Case", 40, 1.55, 380),
    ("Long Holds", "Revolution Case", 25, 0.62, 330),
    ("Long Holds", "AK-47 | Slate (Minimal Wear)", 3, 4.40, 250),
    ("Case Stash", "Fracture Case", 60, 0.48, 290),
    ("Case Stash", "Sticker | FaZe Clan (Holo) | Paris 2023", 8, 1.30, 210),
]
BUFF_QUOTES = {"AK-47 | Redline (Field-Tested)": 0.83, "AWP | Asiimov (Battle-Scarred)": 0.81,
               "★ Karambit | Doppler (Factory New)": 0.85}


def _walk(final_price: float, days: int, rng: random.Random, vol: float) -> list[float]:
    """Backwards random walk ending exactly at ``final_price``."""
    prices = [final_price]
    drift = rng.uniform(-0.0012, 0.0016)
    for _ in range(days - 1):
        prices.append(max(prices[-1] / math.exp(rng.gauss(drift, vol)), 0.01))
    return prices[::-1]


def _sticker_rate(price: float) -> float:
    lo, hi = Config.STICKER_PRICE_LOW, Config.STICKER_PRICE_HIGH
    rmin, rmax = Config.STICKER_RATE_MIN, Config.STICKER_RATE_MAX
    if price <= lo:
        return rmin
    if price >= hi:
        return rmax
    t = (math.log10(price) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return rmin + t * (rmax - rmin)


def seed_if_empty() -> bool:
    if db.query_one("SELECT 1 FROM items LIMIT 1"):
        return False
    seed()
    return True


def seed() -> None:
    rng = random.Random(730)
    today = date.today()
    days = [(today - timedelta(days=HISTORY_DAYS - 1 - i)).isoformat() for i in range(HISTORY_DAYS)]

    # FX history with a gentle wobble.
    fx_rows = []
    for i, day in enumerate(days):
        for code, rate in DEMO_FX.items():
            wobble = 1 + (0 if code == "USD" else 0.025 * math.sin(i / 37 + sum(map(ord, code)) % 7))
            fx_rows.append((day, code, rate * wobble, SOURCE_NOTE))
    db.executemany("INSERT OR REPLACE INTO fx_rates (rate_date, currency, per_usd, source) VALUES (?, ?, ?, ?)", fx_rows)
    fx = {(r[0], r[1]): r[2] for r in fx_rows}

    # Price series (USD) for every item and applied sticker.
    prices: dict[str, float] = {}
    for _, name, _, price, _, extra in DEMO_ITEMS:
        if price is not None:
            prices[name] = price
        for kind, sticker in extra.get("stickers", []):
            prices.setdefault(applied_hash_name(kind, sticker), 145.0 if "Crown" in sticker else 4.2)
    for _, name, _, price, _ in DEMO_CONTENTS:
        prices[name] = price
    series = {name: _walk(p, HISTORY_DAYS, rng, 0.012 if p > 50 else 0.02) for name, p in prices.items()}
    # Per-market price multipliers relative to Steam. The valuation is their median, like the live engine.
    market_ranges = {"steam": (1.0, 1.0, "USD"), "skinport": (0.80, 0.92, "EUR"), "csfloat": (0.86, 0.97, "USD"),
                     "marketcsgo": (0.76, 0.90, "USD"), "waxpeer": (0.79, 0.93, "USD")}
    mults = {name: {m: rng.uniform(lo, hi) for m, (lo, hi, _) in market_ranges.items()} for name in series}
    val_mult = {name: sorted(m.values())[len(m) // 2] for name, m in mults.items()}
    low_mult = {name: min(m.values()) for name, m in mults.items()}  # stickers use the cheapest listing

    snapshot_rows, quote_rows, check_rows = [], [], []
    now = db.utcnow_iso()
    for name, values in series.items():
        for day, usd in zip(days, values):
            snapshot_rows.append((name, "valuation", day, usd * val_mult[name], "USD"))
            for market, (_, _, ccy) in market_ranges.items():
                snapshot_rows.append((name, market, day, round(usd * mults[name][market] * fx[(day, ccy)], 2), ccy))
        last = values[-1]
        for market, (_, _, ccy) in market_ranges.items():
            volume = None if market == "csfloat" else rng.randint(1, 9000)
            quote_rows.append((name, market, round(last * mults[name][market] * fx[(days[-1], ccy)], 2), ccy,
                               volume, None, 0, now))
        if name in BUFF_QUOTES:
            quote_rows.append((name, "manual:Buff163", round(last * BUFF_QUOTES[name] * fx[(days[-1], "CNY")], 1),
                               "CNY", None, None, 1, now))
        check_rows += [(name, "steam", now), (name, "csfloat", now)]

    db.executemany("INSERT OR REPLACE INTO price_snapshots VALUES (?, ?, ?, ?, ?)", snapshot_rows)
    db.executemany("INSERT OR REPLACE INTO market_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", quote_rows)
    db.executemany("INSERT OR REPLACE INTO price_checks VALUES (?, ?, ?)", check_rows)

    # Items.
    containers: dict[str, int] = {}
    held: list[tuple[str, str, int, int, list, float | None]] = []  # (source, name, qty, start index, stickers, custom)
    with db.connect() as conn:
        for index, (source, name, qty, _, days_held, extra) in enumerate(DEMO_ITEMS):
            created = (today - timedelta(days=days_held)).isoformat() + "T12:00:00+00:00"
            category = "container" if extra.get("is_storage") else categorize(name)
            item_id = conn.execute(
                "INSERT INTO items (source, steam_id, asset_id, market_hash_name, name, category, exterior, float_value, "
                "paint_seed, name_tag, quantity, marketable, custom_price, custom_price_currency, purchase_price, "
                "purchase_currency, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, DEMO_OWNER if source == "steam" else None, f"demo-{index}" if source == "steam" else None,
                 name, name, category, exterior_from_name(name), extra.get("float_value"), extra.get("paint_seed"),
                 extra.get("name_tag"), qty, extra.get("marketable", 1), extra.get("custom_price"),
                 "USD" if extra.get("custom_price") else None, extra.get("purchase_price"),
                 "USD" if extra.get("purchase_price") else None, extra.get("notes"), created, created),
            ).lastrowid
            stickers = extra.get("stickers", [])
            conn.executemany(
                "INSERT INTO item_stickers (item_id, slot, kind, name, scrape) VALUES (?, ?, ?, ?, 0)",
                [(item_id, slot, kind, sticker) for slot, (kind, sticker) in enumerate(stickers)],
            )
            if extra.get("is_storage"):
                containers[extra["name_tag"]] = item_id
                conn.execute("UPDATE items SET is_storage = 1 WHERE id = ?", (item_id,))
            held.append((source, name, qty, HISTORY_DAYS - days_held, stickers, extra.get("custom_price")))

        for tag, name, qty, _, days_held in DEMO_CONTENTS:
            created = (today - timedelta(days=days_held)).isoformat() + "T12:00:00+00:00"
            conn.execute(
                "INSERT INTO items (source, market_hash_name, name, category, exterior, quantity, container_id, "
                "created_at, updated_at) VALUES ('manual', ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, name, categorize(name), exterior_from_name(name), qty, containers[tag], created, created),
            )
            held.append(("manual", name, qty, HISTORY_DAYS - days_held, [], None))

    # Portfolio history built from the same synthetic series.
    portfolio_rows = []
    for i, day in enumerate(days):
        totals = {"public": 0.0, "total": 0.0}
        counts = {"public": 0, "total": 0}
        for source, name, qty, start, stickers, custom in held:
            if i < start:
                continue
            unit = custom if custom is not None else (series[name][i] * val_mult[name] if name in series else 0.0)
            names = [s for _, s in stickers]
            raw, craft = 0.0, False
            for kind, sticker in stickers:  # same rules as the live valuation engine
                hash_name = applied_hash_name(kind, sticker)
                loose = series[hash_name][i] * low_mult[hash_name]
                is_craft = names.count(sticker) >= 4
                craft = craft or is_craft
                raw += loose * _sticker_rate(loose) * (Config.STICKER_CRAFT_BONUS if is_craft else 1)
            if raw and unit and custom is None:  # custom prices already include stickers
                cap = unit * Config.STICKER_PREMIUM_CAP * (Config.STICKER_CRAFT_CAP_MULTIPLIER if craft else 1)
                unit += soft_cap(raw, cap)
            for scope in ("total", "public") if source == "steam" else ("total",):
                totals[scope] += unit * qty
                counts[scope] += qty
        for scope in totals:
            if counts[scope]:
                portfolio_rows.append((day, scope, round(totals[scope], 2), counts[scope], "USD"))
    db.executemany("INSERT OR REPLACE INTO portfolio_snapshots VALUES (?, ?, ?, ?, ?)", portfolio_rows)

    settings.set_value("base_currency", "USD")
    settings.set_value("steam_id", "")
    settings.set_value("last_snapshot", db.utcnow_iso())
