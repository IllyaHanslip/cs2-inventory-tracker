"""Portfolio tables: valuation per item, container roll-ups, filters, sorting and
the cross-market comparison. Everything is computed with pandas from SQLite."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from . import catalogue, db, evidence, history, images, sanity, tags
from .classify import CATEGORIES
from .currency import last_source
from .markets import MANUAL_PREFIX, SOURCES, market_label

CATEGORY_FILTERS = (*CATEGORIES, "in_container")
SORT_COLUMNS = {
    "name": "name",
    "value": "display_value",
    "price": "unit_value",
    "change_24h": "change_24h",
    "change_7w": "change_7w",
    "quantity": "quantity",
    "float": "float_value",
}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def _load_stickers() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for row in db.query("SELECT item_id, slot, kind, name, scrape FROM item_stickers ORDER BY item_id, kind, slot"):
        out.setdefault(row["item_id"], []).append(row)
    return out


def container_label(name: str, name_tag: str | None) -> str:
    return f"{name} “{name_tag}”" if name_tag else name


# ---------------------------------------------------------------------------
# Main enriched table
# ---------------------------------------------------------------------------
def build_frame(valuator, scope: str = "total", with_changes: bool = True) -> pd.DataFrame:
    """One row per item with unit price, sticker premium, value, contents and changes."""
    all_items = pd.DataFrame(db.query("SELECT * FROM items ORDER BY id"))
    if all_items.empty:
        return all_items
    labels = {
        row.id: container_label(row.name, row.name_tag if isinstance(row.name_tag, str) else None)
        for row in all_items[all_items["category"] == "container"].itertuples()
    }
    with_icon = all_items[[isinstance(i, str) and i != "" for i in all_items["icon_url"]]]
    icons = with_icon.drop_duplicates("market_hash_name").set_index("market_hash_name")["icon_url"].to_dict()

    items = all_items[all_items["source"] == "steam"].copy() if scope == "public" else all_items.copy()
    if items.empty:
        return items
    # Manual items borrow the icon of a Steam item with the same name, else the catalogue's picture.
    missing = {name for icon, name in zip(items["icon_url"], items["market_hash_name"])
               if not (isinstance(icon, str) and icon) and name not in icons}
    if missing:
        icons.update(images.lookup(missing))
    items["icon_url"] = [
        icon if isinstance(icon, str) and icon else icons.get(name)
        for icon, name in zip(items["icon_url"], items["market_hash_name"])
    ]
    stickers = _load_stickers()

    evidence_rows = evidence.load_all() if valuator.prefs.get("sticker_evidence", True) else {}
    unit_prices, sources, premiums, breakdowns, summaries, purchase, bases = [], [], [], [], [], [], []
    for row in items.itertuples(index=False):
        applied = stickers.get(row.id, [])
        custom = valuator.custom_price(_num(row.custom_price), row.custom_price_currency)
        market = valuator.price(row.market_hash_name)
        found = evidence_rows.get(row.id)
        basis = None
        if custom is not None:
            unit_prices.append(custom)
            sources.append("custom")
        elif applied and evidence.usable(found, applied) and \
                (value := valuator.fx.convert(found["value"], found["currency"], valuator.base)) is not None:
            # Real CSFloat listings/sales with the same stickers beat the sticker formula.
            unit_prices.append(value)
            sources.append("evidence")
            basis = found["basis"]
        else:
            unit_prices.append(market)
            sources.append(valuator.strategy if market is not None else None)
        bases.append(basis)
        premium, breakdown, summary = valuator.applied_premium(applied, market if sources[-1] == "evidence" else unit_prices[-1])
        whole_item = sources[-1] in ("custom", "evidence")
        summary["included"] = whole_item and summary["raw"] > 0
        if whole_item:
            # A custom or evidence price is the value of the whole item, stickers included.
            premium = 0.0
            summary.update(capped=False, no_base=False)
            for detail in breakdown:
                detail["counted"] = 0.0
        premiums.append(premium)
        breakdowns.append(breakdown)
        summaries.append(summary)
        purchase.append(valuator.custom_price(_num(row.purchase_price), row.purchase_currency))

    items["unit_price"] = pd.Series(unit_prices, index=items.index, dtype="float64")
    items["market_price"] = items["market_hash_name"].map(lambda n: valuator.price(n))
    items["price_source"] = sources
    items["price_basis"] = bases
    items["sticker_premium"] = premiums
    items["stickers"] = breakdowns
    items["sticker_loose_total"] = [s["loose_total"] for s in summaries]
    items["sticker_raw_premium"] = [s["raw"] for s in summaries]
    items["sticker_cap"] = [s["cap"] for s in summaries]
    items["sticker_capped"] = [s["capped"] for s in summaries]
    items["sticker_no_base"] = [s["no_base"] for s in summaries]
    items["sticker_included"] = [s["included"] for s in summaries]
    # Market value: what the item costs to buy - the valuation price plus counted sticker value.
    # Kept for reference (Buy view, item breakdown); everything else uses the selling price below.
    has_value = items["unit_price"].notna() | (items["sticker_premium"] > 0)
    items["market_unit_value"] = (items["unit_price"].fillna(0) + items["sticker_premium"]).where(has_value)
    items["market_total_value"] = items["market_unit_value"] * items["quantity"]

    # Every market's current listing (base currency), cheapest first - for the buy view and market filters.
    quote_lists: dict[str, list[dict]] = {}
    if not valuator.quotes.empty:
        for row in valuator.quotes.sort_values("price_base").itertuples():
            quote_lists.setdefault(row.market_hash_name, []).append(
                {"market": row.market, "label": market_label(row.market), "price": float(row.price_base),
                 "url": row.url, "net": valuator.net(row.market, float(row.price_base)),
                 "cash_out": valuator.cash_out(row.market), "basis": "listing"})

    def quotes_for(name: str) -> list[dict]:
        # No usable listing: recent sales, or for a slab the sticker inside (see Valuator.fallback_quotes).
        found = quote_lists.get(name)
        if found:
            return found
        return [{**q, "label": market_label(q["market"])} for q in valuator.fallback_quotes(name)]

    items["quotes"] = items["market_hash_name"].map(quotes_for)

    # Selling price: the most you'd receive on any market after its selling and withdrawal fees.
    # Steam counts too (flagged "wallet": that money can't be withdrawn). Listing-priced items sell
    # at each market's cheapest listing plus their sticker premium; custom and CSFloat-comparables
    # values are whole-item prices, sold at that price on each market.
    enabled = list(valuator.prefs["enabled_markets"])
    sell_lists = []
    for row in items.itertuples(index=False):
        value = _num(row.market_unit_value)
        if row.price_source in ("custom", "evidence"):
            markets = list(dict.fromkeys(enabled + [q["market"] for q in row.quotes]))
            options = valuator.sell_options(row.quotes, whole_value=value, markets=markets) if value else []
        else:
            options = valuator.sell_options(row.quotes, extra=row.sticker_premium or 0.0)
        for option in options:
            option["label"] = market_label(option["market"])
        sell_lists.append(options)
    best = [options[0] if options else None for options in sell_lists]
    cash = [next((o for o in options if not o["wallet"]), None) for options in sell_lists]
    items["sell_quotes"] = sell_lists
    items["unit_value"] = pd.Series([b["net"] if b else None for b in best], index=items.index, dtype="float64")
    items["sell_market"] = [b["market"] if b else None for b in best]
    items["value_basis"] = [b["basis"] if b else None for b in best]   # listing / sales / sticker_inside / value

    # What the price check knows about each item: recent sales, listings set aside, and for
    # Sticker Slabs the sticker inside (a slab can be cracked open to get it back).
    def sales_info(name: str) -> dict | None:
        info = valuator.sales.get(name)
        if not info:
            return None
        label = "CSFloat reference price" if info["source"] == "csfloat_ref" else market_label(info["source"])
        return {**info, "label": label, "trusted": name in valuator.trusted_sales}

    def inside_info(name: str) -> dict | None:
        inner = sanity.sticker_inside(name)
        if not inner:
            return None
        options = valuator.sell_options(quotes_for(inner))
        best_inner = options[0] if options else None
        return {"name": inner, "market_value": valuator.price(inner),
                "value": best_inner["net"] if best_inner else None,
                "market": market_label(best_inner["market"]) if best_inner else None,
                "market_key": best_inner["market"] if best_inner else None,
                "wallet": best_inner["wallet"] if best_inner else False}

    items["sales"] = items["market_hash_name"].map(sales_info)
    items["set_aside"] = items["market_hash_name"].map(
        lambda n: [{**a, "label": market_label(a["market"])} for a in valuator.set_aside.get(n, [])])
    items["sticker_inside"] = items["market_hash_name"].map(inside_info)
    # Event, collection, type, weapon and rarity, for grouping (no download here: see catalogue.item_meta).
    meta = catalogue.item_meta(allow_fetch=False)
    items["tags"] = [tags.item_tags(name, category, rarity, meta)
                     for name, category, rarity in zip(items["market_hash_name"], items["category"], items["rarity"])]

    # Selling everything at once: a market only buys so many of one item. Each takes about what it
    # sells in BULK_SELL_DAYS (Valuator.bulk_plan), so 531 capsules aren't all counted at the price
    # of a market that sells 8 a week. Plain copies of an item are pooled across rows (say several
    # containers), so they share each market's sales instead of each counting them in full.
    for name, options in zip(items["market_hash_name"], sell_lists):
        for option in options:
            source = sanity.sticker_inside(name) if option.get("basis") == "sticker_inside" else name
            option["capacity"] = valuator.sell_capacity(source, option["market"])
    plain = [(s not in ("custom", "evidence")) and not (p or 0) > 0
             for s, p in zip(items["price_source"], items["sticker_premium"])]
    pooled = items[plain].groupby("market_hash_name")["quantity"].sum()
    plans: dict[Any, tuple] = {}
    realised, cash_realised, bulk_plans, bulk_units = [], [], [], []
    for row, options, is_plain in zip(items.itertuples(index=False), sell_lists, plain):
        units = int(pooled[row.market_hash_name]) if is_plain else int(row.quantity)
        key = ("plain", row.market_hash_name) if is_plain else ("row", row.id)
        if key not in plans:
            total, plan = valuator.bulk_plan(row.market_hash_name, options, units)
            cash_total, cash_plan = valuator.bulk_plan(row.market_hash_name, [o for o in options if not o["wallet"]], units)
            for step in plan:
                step["label"] = market_label(step["market"])
            plans[key] = (total / units if plan else None, cash_total / units if cash_plan else None, plan)
        each, cash_each, plan = plans[key]
        realised.append(each)
        cash_realised.append(cash_each)
        bulk_plans.append(plan)
        bulk_units.append(units)
    items["bulk_plan"] = bulk_plans
    items["bulk_units"] = bulk_units   # copies pooled for the estimate (all rows of a plain item)
    # True when selling them all can't happen at the best price alone (only matters for 2+ copies).
    items["bulk_limited"] = [n > 1 and (len(p) > 1 or any(s.get("overflow") for s in p))
                             for n, p in zip(bulk_units, bulk_plans)]
    items["realised_unit"] = pd.Series(realised, index=items.index, dtype="float64")
    items["total_value"] = items["realised_unit"] * items["quantity"]
    items["purchase_value"] = pd.Series(purchase, index=items.index, dtype="float64") * items["quantity"]
    items["profit"] = items["total_value"] - items["purchase_value"]
    # Cash-out: the same, counting only markets whose money you can withdraw.
    items["payout_unit"] = pd.Series([c["net"] if c else None for c in cash], index=items.index, dtype="float64")
    items["payout_market"] = [c["market"] if c else None for c in cash]
    items["payout_value"] = pd.Series(cash_realised, index=items.index, dtype="float64") * items["quantity"]

    # Containers: roll up the value of everything stored inside them (within this scope).
    items["is_container"] = items["category"] == "container"
    inside = items[items["container_id"].notna()]
    rollup = inside.groupby(inside["container_id"].astype("int64")).agg(
        contents_count=("quantity", "sum"),
        contents_value=("total_value", lambda s: s.fillna(0).sum()),
    )
    items["contents_count"] = items["id"].map(rollup["contents_count"]).fillna(0).astype(int)
    items["contents_value"] = items["id"].map(rollup["contents_value"]).fillna(0.0)
    container_total = (items["total_value"].fillna(0) + items["contents_value"]).where(
        items["total_value"].notna() | (items["contents_count"] > 0)
    )
    items["display_value"] = items["total_value"].where(~items["is_container"], container_total)
    items["container_name"] = items["container_id"].map(lambda cid: labels.get(int(cid)) if pd.notna(cid) else None)

    deals = valuator.best_deals()
    items["best_market"] = items["market_hash_name"].map(lambda n: deals.get(n, {}).get("market"))
    items["best_price"] = items["market_hash_name"].map(lambda n: deals.get(n, {}).get("price"))
    counts = valuator.quotes.groupby("market_hash_name").size() if not valuator.quotes.empty else pd.Series(dtype=int)
    items["quote_count"] = items["market_hash_name"].map(counts).fillna(0).astype(int)

    if with_changes:
        changes = history.price_changes(items["market_hash_name"].unique().tolist(), valuator)
        changes = changes.drop_duplicates("market_hash_name").set_index("market_hash_name")
        for col in history.CHANGE_WINDOWS:
            items[col] = items["market_hash_name"].map(changes[col]).where(~items["price_source"].isin(["custom", "evidence"]))
    else:
        for col in history.CHANGE_WINDOWS:
            items[col] = np.nan

    sticker_text = items["stickers"].map(lambda s: " ".join(a["name"] for a in s))
    items["search_text"] = (
        items["name"].fillna("") + " " + items["market_hash_name"] + " " + items["name_tag"].fillna("") + " "
        + items["notes"].fillna("") + " " + sticker_text
    ).str.lower()
    return items


# ---------------------------------------------------------------------------
# Filtering & sorting engine
# ---------------------------------------------------------------------------
def apply_filters(
    df: pd.DataFrame,
    categories: list[str] | None = None,
    search: str = "",
    sort: str = "value",
    order: str = "desc",
    container_id: int | None = None,
    include_contents: bool = True,
) -> pd.DataFrame:
    """Category chips combine with OR. Items stored inside containers are listed with
    everything else unless ``include_contents`` is off (then only the "in_container"
    chip, a search, or a specific container shows them)."""
    if df.empty:
        return df
    categories = [c for c in (categories or []) if c in CATEGORY_FILTERS]
    search = (search or "").strip().lower()
    mask = pd.Series(True, index=df.index)

    if container_id is not None:
        mask &= df["container_id"] == container_id
    else:
        if not include_contents and "in_container" not in categories and not search:
            mask &= df["container_id"].isna()
        if categories:
            plain = [c for c in categories if c != "in_container"]
            chosen = df["category"].isin(plain)
            if "in_container" in categories:
                chosen |= df["container_id"].notna()
            mask &= chosen
    if search:
        for term in search.split():
            mask &= df["search_text"].str.contains(term, regex=False)

    out = df[mask]
    column = SORT_COLUMNS.get(sort, "display_value")
    if column == "display_value" and include_contents and container_id is None:
        column = "total_value"  # contents are listed separately, so rank containers by their own value
    ascending = order == "asc"
    if column == "name":
        return out.sort_values("name", ascending=ascending, key=lambda s: s.str.lower(), kind="stable")
    return out.sort_values(column, ascending=ascending, na_position="last", kind="stable")


# ---------------------------------------------------------------------------
# Serialisation & summaries
# ---------------------------------------------------------------------------
def _py(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NaT:
        return None
    return value


ITEM_FIELDS = (
    "id", "source", "steam_id", "asset_id", "market_hash_name", "name", "category", "item_type", "weapon",
    "rarity", "rarity_color", "exterior", "float_value", "paint_seed", "name_tag", "quantity", "container_id",
    "container_name", "is_container", "is_storage", "contents_count", "contents_value", "inspect_link",
    "tradable", "marketable", "custom_price", "custom_price_currency", "purchase_price", "purchase_currency",
    "notes", "unit_price", "market_price", "price_source", "price_basis", "sticker_premium", "sticker_loose_total", "sticker_raw_premium",
    "sticker_cap", "sticker_capped", "sticker_no_base", "sticker_included", "unit_value", "total_value", "display_value",
    "purchase_value", "profit", "change_24h", "change_7w", "best_market", "best_price", "quote_count",
    "quotes", "payout_unit", "payout_market", "payout_value", "stickers", "created_at", "updated_at",
    "market_unit_value", "market_total_value", "sell_market", "sell_quotes",
    "value_basis", "sales", "set_aside", "sticker_inside", "bulk_plan", "bulk_units", "bulk_limited",
    "realised_unit", "tags", "steam_count", "game_count",
)


def to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    for rec in df.to_dict("records"):
        item = {field: _py(rec.get(field)) for field in ITEM_FIELDS}
        if item["container_id"] is not None:
            item["container_id"] = int(item["container_id"])
        for count in ("steam_count", "game_count"):
            if item[count] is not None:
                item[count] = int(item[count])
        item["category_label"] = CATEGORIES.get(item["category"], "Other")
        item["is_storage"] = bool(item["is_storage"])
        item["label"] = container_label(item["name"], item["name_tag"]) if item["is_container"] else item["name"]
        item["best_market_label"] = market_label(item["best_market"]) if item["best_market"] else None
        item["payout_market_label"] = market_label(item["payout_market"]) if item["payout_market"] else None
        item["sell_market_label"] = market_label(item["sell_market"]) if item["sell_market"] else None
        item["sell_quotes"] = rec.get("sell_quotes") or []
        item["set_aside"] = rec.get("set_aside") or []
        item["bulk_plan"] = rec.get("bulk_plan") or []
        item["tags"] = rec.get("tags") or {}
        item["bulk_limited"] = bool(rec.get("bulk_limited"))
        icon = rec.get("icon_url")
        item["icon"] = images.icon_src(icon)
        item["stickers"] = [{k: _py(v) for k, v in s.items()} for s in (rec.get("stickers") or [])]
        item["quotes"] = rec.get("quotes") or []
        records.append(item)
    return records


def summarize(df: pd.DataFrame, valuator, scope: str) -> dict[str, Any]:
    if df.empty:
        total = 0.0
        stats = {"item_count": 0, "unique_items": 0, "unpriced_items": 0, "sticker_premium": 0.0,
                 "containers": 0, "storage_containers": 0, "items_in_containers": 0, "value_in_containers": 0.0,
                 "purchase_value": None, "profit": None, "cash_out_value": 0.0, "cash_out_missing": 0,
                 "market_value": 0.0}
    else:
        total = float(df["total_value"].fillna(0).sum())
        purchase = df["purchase_value"].dropna()
        stats = {
            "item_count": int(df["quantity"].sum()),
            "unique_items": int(df["market_hash_name"].nunique()),
            "unpriced_items": int((df["total_value"].isna() & ~df["is_container"]).sum()),
            "sticker_premium": float((df["sticker_premium"] * df["quantity"]).sum()),
            "containers": int(df["is_container"].sum()),
            "storage_containers": int(((df["category"] == "container") & df["container_id"].isna()
                                       & ((df["is_storage"] == 1) | (df["contents_count"] > 0))).sum()),
            "items_in_containers": int(df.loc[df["container_id"].notna(), "quantity"].sum()),
            "value_in_containers": float(df.loc[df["container_id"].notna(), "total_value"].fillna(0).sum()),
            "purchase_value": float(purchase.sum()) if not purchase.empty else None,
            "cash_out_value": float(df["payout_value"].fillna(0).sum()),
            # For reference: what everything would cost to buy at current market prices.
            "market_value": float(df["market_total_value"].fillna(0).sum()),
            # Priced items with no market you can cash out from (e.g. only a Steam price).
            "cash_out_missing": int((df["total_value"].notna() & df["payout_value"].isna()).sum()),
            "profit": float(df.loc[purchase.index, "profit"].fillna(0).sum()) if not purchase.empty else None,
        }
    changes = history.portfolio_changes(scope, total, valuator.fx, valuator.base, stats["item_count"])
    for col, value in changes.items():
        if value is None:
            # Items were added or removed at every step (say, a container filled in today), so
            # the saved totals don't compare like with like: use how your items' prices moved.
            changes[col] = held_change(df, col)
    return {"scope": scope, "currency": valuator.base, "total_value": total, **stats, **changes}


def held_change(df: pd.DataFrame, col: str) -> float | None:
    """Value-weighted price change (%) of the items you hold that have one; custom prices stay put."""
    if df.empty or col not in df:
        return None
    rows = df[df[col].notna() & df["total_value"].notna() & (df["price_source"] != "custom") & (df[col] > -100)]
    if rows.empty:
        return None
    now = float(rows["total_value"].sum())
    before = float((rows["total_value"] / (1 + rows[col] / 100)).sum())
    return (now - before) / before * 100 if before > 0 else None


# ---------------------------------------------------------------------------
# Storage containers
# ---------------------------------------------------------------------------
def storage_overview(df: pd.DataFrame, capacity: int) -> dict[str, Any]:
    """Every top-level storage container with fill level, value and a preview of its contents."""
    empty_summary = {"containers": 0, "items_stored": 0, "value_stored": 0.0, "capacity": 0}
    if df.empty:
        return {"containers": [], "summary": empty_summary, "capacity": capacity}
    storage = df[
        (df["category"] == "container") & df["container_id"].isna()
        & ((df["is_storage"] == 1) | (df["contents_count"] > 0))
    ]
    inside = df[df["container_id"].notna()]
    records = []
    for record in to_records(storage):
        contents = inside[inside["container_id"] == record["id"]].sort_values("total_value", ascending=False, na_position="last")
        record["capacity"] = capacity
        record["preview"] = [{"id": int(r.id), "name": r.name, "quantity": int(r.quantity)}
                             for r in contents.head(3).itertuples()]
        record["kinds"] = int(len(contents))
        records.append(record)
    records.sort(key=lambda r: (-(r["contents_value"] or 0), r["label"].lower()))
    summary = {
        "containers": len(records),
        "items_stored": int(sum(r["contents_count"] for r in records)),
        "value_stored": float(sum(r["contents_value"] or 0 for r in records)),
        "capacity": capacity * len(records),
    }
    return {"containers": records, "summary": summary, "capacity": capacity}


# ---------------------------------------------------------------------------
# Market comparison & best deals
# ---------------------------------------------------------------------------
def market_comparison(valuator, scope: str = "total") -> dict[str, Any]:
    df = build_frame(valuator, scope, with_changes=False)
    enabled = [m for m in SOURCES if m in valuator.prefs["enabled_markets"]]
    quotes = valuator.quotes
    manual = sorted(m for m in quotes["market"].unique() if str(m).startswith(MANUAL_PREFIX)) if not quotes.empty else []
    markets = [
        {"key": key, "label": market_label(key), "manual": key.startswith(MANUAL_PREFIX),
         "cash_out": valuator.cash_out(key)} for key in enabled + manual
    ]
    fx_info = _fx_info(valuator, quotes)
    if df.empty:
        return {"currency": valuator.base, "markets": markets, "rows": [], "fx": fx_info}

    owned = df.groupby("market_hash_name").agg(
        quantity=("quantity", "sum"), icon_url=("icon_url", "first"), category=("category", "first")
    )
    quotes = quotes[quotes["market_hash_name"].isin(owned.index)]
    matrix = quotes.pivot_table(index="market_hash_name", columns="market", values="price_base", aggfunc="min")
    if not matrix.empty:
        best_price = matrix.min(axis=1)
        best_market = matrix.idxmin(axis=1)
        spread = ((matrix.max(axis=1) - best_price) / best_price * 100).where(matrix.count(axis=1) >= 2)
        steam_saving = matrix["steam"] - best_price if "steam" in matrix.columns else pd.Series(dtype=float)
    else:
        best_price = best_market = spread = steam_saving = pd.Series(dtype=float)
    details = {name: grp for name, grp in quotes.groupby("market_hash_name")}

    rows = []
    for name, info in owned.iterrows():
        prices = {}
        if name in details:
            for q in details[name].itertuples():
                volume = _py(q.volume)
                prices[q.market] = {
                    "price_base": float(q.price_base),
                    "price": float(q.price),
                    "currency": q.currency,
                    "url": q.url,
                    "volume": int(volume) if volume is not None else None,
                    "fetched_at": q.fetched_at,
                    "net": valuator.net(q.market, float(q.price_base)),
                    "cash_out": valuator.cash_out(q.market),
                }
        icon = info["icon_url"]
        rows.append({
            "market_hash_name": name,
            "category": info["category"],
            "quantity": int(info["quantity"]),
            "icon": images.icon_src(icon),
            "prices": prices,
            "valuation": valuator.price(name),
            "best_market": best_market.get(name) if name in best_market.index else None,
            "best_market_label": market_label(best_market[name]) if name in best_market.index else None,
            "best_price": _py(best_price.get(name)) if name in best_price.index else None,
            "spread_pct": _py(spread.get(name)) if name in spread.index else None,
            "steam_saving": _py(steam_saving.get(name)) if name in steam_saving.index else None,
        })
    return {"currency": valuator.base, "markets": markets, "rows": rows, "fx": fx_info}


def _fx_info(valuator, quotes: pd.DataFrame) -> dict[str, Any]:
    used = sorted(set(quotes["currency"]) if not quotes.empty else set())
    used = [c for c in dict.fromkeys(["USD", "EUR", "CNY", "GBP", *used]) if c != valuator.base]
    rates = {c: valuator.fx.convert(1.0, c, valuator.base) for c in used}
    return {
        "base": valuator.base,
        "date": valuator.fx.latest_date,
        "source": (last_source() or {}).get("source"),
        "rates": {c: r for c, r in rates.items() if r is not None},
    }
