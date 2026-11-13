"""JSON API consumed by the dashboard (all routes are under /api)."""
from __future__ import annotations

import io
import re
from typing import Any

from flask import Blueprint, Response, jsonify, request

from config import Config

from . import catalogue, comparables, db, evidence, fees, history, images, portfolio, settings, storage
from .classify import APPLIED_KINDS, CATEGORIES, EXTERIORS, applied_hash_name, categorize, exterior_from_float, exterior_from_name
from .currency import FxConverter, last_source
from .http_client import ApiError, RateLimitError
from .jobs import JobBusy, jobs, refresh_job, sync_job
from .markets import MANUAL_PREFIX, SOURCES, Quote, market_label
from .refresh import save_quotes
from .steam_inventory import resolve_steam_id
from .valuation import Valuator

bp = Blueprint("api", __name__, url_prefix="/api")


class NotFoundError(Exception):
    pass


@bp.errorhandler(ValueError)
def _bad_request(exc):
    return jsonify(error=str(exc)), 400


@bp.errorhandler(NotFoundError)
def _not_found(exc):
    return jsonify(error=str(exc) or "Not found"), 404


@bp.errorhandler(JobBusy)
def _busy(exc):
    return jsonify(error=str(exc)), 409


@bp.errorhandler(RateLimitError)
def _rate_limited(exc):
    return jsonify(error=str(exc)), 429


@bp.errorhandler(ApiError)
def _upstream(exc):
    return jsonify(error=str(exc)), 502


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _scope() -> str:
    scope = request.args.get("scope", "total")
    if scope not in ("public", "total"):
        raise ValueError("scope must be 'public' or 'total'")
    return scope


# ---------------------------------------------------------------------------
# Status & settings
# ---------------------------------------------------------------------------
@bp.get("/status")
def status():
    prefs = settings.get_all()
    fx = FxConverter.load()
    stored = {r["market"]: r for r in db.query(
        "SELECT market, COUNT(*) AS quotes, MAX(fetched_at) AS last FROM market_prices WHERE is_manual = 0 GROUP BY market")}
    attempts = settings.get("market_attempts") or {}
    markets = []
    for key, source in SOURCES.items():
        usable, reason = source.available()
        markets.append({"key": key, "label": source.label, "enabled": key in prefs["enabled_markets"],
                        "available": usable, "reason": reason, "bulk": source.bulk,
                        "quotes": stored.get(key, {}).get("quotes", 0), "last_fetched": stored.get(key, {}).get("last"),
                        "last_attempt": attempts.get(key)})
    newest = max((r["last"] for r in stored.values()), default=None)
    overrides = prefs.get("selling_fees") or {}
    manual = [r["market"] for r in db.query("SELECT DISTINCT market FROM market_prices WHERE is_manual = 1 ORDER BY market")]
    fee_rows = []
    for key in [k for k in SOURCES if k in prefs["enabled_markets"]] + manual:
        p = fees.profile(key, overrides)
        fee_rows.append({"key": key, "label": market_label(key), "sale": p["sale"], "withdraw": p["withdraw"],
                         "cash_out": p["cash_out"], "mode": p["mode"], "note": p["note"],
                         "tiered": "sale_high" in p, "custom": key in overrides})
    return jsonify(
        demo=Config.DEMO,
        settings=prefs,
        keys={"csfloat_api_key": bool(Config.CSFLOAT_API_KEY),
              "legacy_steam_api_key": Config.LEGACY_STEAM_API_KEY_SET},
        markets=markets,
        currencies=fx.currencies(),
        fx={"date": fx.latest_date, "source": (last_source() or {}).get("source")},
        last_steam_sync=settings.get("last_steam_sync"),
        last_snapshot=settings.get("last_snapshot"),
        prices_updated=newest,
        fees={"checked": fees.FEES_CHECKED, "markets": fee_rows},
        job=jobs.status(),
        categories=CATEGORIES,
        exteriors=EXTERIORS,
        applied_kinds=list(APPLIED_KINDS),
        skinport_currency=Config.SKINPORT_CURRENCY,
    )


@bp.put("/settings")
def update_settings():
    data = _body()
    if data.get("steam_id"):
        data["steam_id"] = resolve_steam_id(data["steam_id"])
    known = set(FxConverter.load().currencies())
    before = set(settings.get_all()["enabled_markets"])
    updated = settings.update(data, known_currencies=known)
    job = None
    # Switching a market on, or turning sticker comparisons back on, fetches its prices right away.
    turned_on = set(updated["enabled_markets"]) - before or data.get("sticker_evidence") is True
    if turned_on and db.query_one("SELECT 1 FROM items LIMIT 1") and not jobs.busy():
        job = jobs.start("refresh", "Price refresh", refresh_job).to_dict()
    return jsonify(settings=updated, job=job)


# ---------------------------------------------------------------------------
# Jobs: Steam sync, price refresh, snapshots
# ---------------------------------------------------------------------------
@bp.post("/steam/sync")
def steam_sync():
    raw = _body().get("steam_id") or settings.get("steam_id")
    if not raw:
        raise ValueError("Enter your SteamID64 or Steam profile URL first.")
    steam_id = resolve_steam_id(raw)
    job = jobs.start("sync", "Steam inventory sync", sync_job, steam_id)
    return jsonify(job=job.to_dict()), 202


@bp.post("/prices/refresh")
def prices_refresh():
    force = bool(_body().get("force"))
    job = jobs.start("refresh", "Price refresh", refresh_job, force)
    return jsonify(job=job.to_dict()), 202


@bp.get("/jobs/current")
def job_status():
    return jsonify(job=jobs.status())


@bp.post("/jobs/cancel")
def job_cancel():
    return jsonify(cancelled=jobs.cancel())


@bp.post("/snapshot")
def snapshot():
    return jsonify(snapshot=history.take_snapshot())


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------
@bp.get("/items")
def list_items():
    scope = _scope()
    valuator = Valuator()
    frame = portfolio.build_frame(valuator, scope)
    categories = [c for c in request.args.get("category", "").split(",") if c]
    include_contents = request.args.get("contents", "show") != "hide"
    filtered = portfolio.apply_filters(
        frame,
        categories=categories,
        search=request.args.get("q", ""),
        sort=request.args.get("sort", "value"),
        order=request.args.get("order", "desc"),
        include_contents=include_contents,
    )
    counts: dict[str, int] = {}
    if not frame.empty:
        listed = frame if include_contents else frame[frame["container_id"].isna()]
        counts = {k: int(v) for k, v in listed["category"].value_counts().items()}
        counts["in_container"] = int(frame["container_id"].notna().sum())
    return jsonify(
        scope=scope,
        summary=portfolio.summarize(frame, valuator, scope),
        counts=counts,
        items=portfolio.to_records(filtered),
    )


def _get_item(item_id: int) -> dict[str, Any]:
    item = db.query_one("SELECT * FROM items WHERE id = ?", (item_id,))
    if not item:
        raise NotFoundError("Item not found")
    return item


def _text(value: Any, label: str, max_len: int, required: bool = False) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        if required:
            raise ValueError(f"{label} is required")
        return None
    if len(text) > max_len:
        raise ValueError(f"{label} must be at most {max_len} characters")
    return text


def _number(value: Any, label: str, lo: float | None = None, hi: float | None = None, integer: bool = False):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a {'whole ' if integer else ''}number") from None
    if number != number or (lo is not None and number < lo) or (hi is not None and number > hi):
        raise ValueError(f"{label} must be between {lo} and {hi}" if hi is not None else f"{label} must be at least {lo}")
    return number


def _currency(value: Any, label: str) -> str | None:
    code = str(value or "").strip().upper()
    if not code:
        return None
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"{label} must be a 3-letter currency code")
    return code


def _parse_stickers(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 6:
        raise ValueError("Stickers must be a list of at most 6 entries")
    out, slots = [], {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Each sticker must be an object")
        name = _text(entry.get("name"), "Sticker name", 200)
        if not name:
            continue
        kind = entry.get("kind") or "sticker"
        if kind not in APPLIED_KINDS:
            raise ValueError(f"Sticker kind must be one of: {', '.join(APPLIED_KINDS)}")
        prefix = APPLIED_KINDS[kind]
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix):].strip()
        scrape = _number(entry.get("scrape"), "Sticker scrape", 0, 100) or 0.0
        scrape = scrape / 100 if scrape > 1 else scrape
        slot = slots.get(kind, 0)
        slots[kind] = slot + 1
        out.append({"kind": kind, "name": name, "scrape": round(scrape, 4), "slot": slot})
    return out


STEAM_EDITABLE = ("float_value", "paint_seed", "custom_price", "custom_price_currency",
                  "purchase_price", "purchase_currency", "notes")


def _parse_item(data: dict[str, Any], existing: dict[str, Any] | None) -> tuple[dict[str, Any], list | None]:
    is_steam = existing is not None and existing["source"] == "steam"
    creating = existing is None
    values: dict[str, Any] = {}

    def present(key: str) -> bool:
        return creating or key in data

    if not is_steam:
        if present("market_hash_name"):
            name = _text(data.get("market_hash_name"), "Item name", 200, required=True)
            values["market_hash_name"] = values["name"] = name
        name = values.get("market_hash_name") or existing["market_hash_name"]
        if present("category"):
            category = data.get("category") or categorize(name)
            if category not in CATEGORIES:
                raise ValueError(f"Category must be one of: {', '.join(CATEGORIES)}")
            values["category"] = category
        if present("exterior"):
            exterior = data.get("exterior") or None
            if exterior and exterior not in EXTERIORS:
                raise ValueError(f"Wear rating must be one of: {', '.join(EXTERIORS)}")
            values["exterior"] = exterior or exterior_from_name(name)
        if present("quantity"):
            values["quantity"] = _number(data.get("quantity", 1), "Quantity", 1, 1_000_000, integer=True) or 1
        if present("name_tag"):
            values["name_tag"] = _text(data.get("name_tag"), "Name tag", 100)
        if present("container_id"):
            values["container_id"] = _number(data.get("container_id"), "Container", 1, integer=True)

    if present("float_value"):
        values["float_value"] = _number(data.get("float_value"), "Float value", 0, 1)
    if present("paint_seed"):
        values["paint_seed"] = _number(data.get("paint_seed"), "Pattern seed", 0, 100_000, integer=True)
    if present("custom_price"):
        values["custom_price"] = _number(data.get("custom_price"), "Custom price", 0)
    if present("custom_price_currency"):
        values["custom_price_currency"] = _currency(data.get("custom_price_currency"), "Custom price currency")
    if present("purchase_price"):
        values["purchase_price"] = _number(data.get("purchase_price"), "Purchase price", 0)
    if present("purchase_currency"):
        values["purchase_currency"] = _currency(data.get("purchase_currency"), "Purchase currency")
    if present("notes"):
        values["notes"] = _text(data.get("notes"), "Notes", 2000)

    # Derive the wear rating from the float when it wasn't chosen explicitly.
    category = values.get("category") or (existing or {}).get("category")
    if not is_steam and category == "weapon_skin" and not values.get("exterior") and values.get("float_value") is not None:
        values["exterior"] = exterior_from_float(values["float_value"])

    stickers = None if is_steam or (not creating and "stickers" not in data) else _parse_stickers(data.get("stickers"))
    return values, stickers


def _replace_stickers(conn, item_id: int, stickers: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM item_stickers WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT INTO item_stickers (item_id, slot, kind, name, scrape) VALUES (?, ?, ?, ?, ?)",
        [(item_id, s["slot"], s["kind"], s["name"], s["scrape"]) for s in stickers],
    )


@bp.post("/items")
def create_item():
    data = _body()
    values, stickers = _parse_item(data, None)
    now = db.utcnow_iso()
    values.setdefault("quantity", 1)
    values.update(source="manual", created_at=now, updated_at=now, marketable=1, tradable=1)
    if values.get("category") == "container":
        values["is_storage"] = int(bool(data.get("is_storage")) or values["market_hash_name"] == storage.STORAGE_UNIT)
    with db.connect() as conn:
        container_id = values.get("container_id")
        storage.check_placement(conn, None, values, container_id)
        if container_id is not None:
            storage.check_capacity(conn, container_id, values["quantity"])
            storage.mark_storage(conn, container_id)
        item_id, stacked = storage.add_item(conn, values, stickers or [])
    images.ensure([values["market_hash_name"]])
    return jsonify(item=_get_item(item_id), stacked=stacked), 201


@bp.put("/items/<int:item_id>")
def update_item(item_id: int):
    existing = _get_item(item_id)
    values, stickers = _parse_item(_body(), existing)
    merged = {**existing, **values}
    with db.connect() as conn:
        storage.check_placement(conn, item_id, merged, merged.get("container_id"))
        if merged.get("container_id") is not None and ("container_id" in values or "quantity" in values):
            storage.check_capacity(conn, merged["container_id"], merged["quantity"], exclude_id=item_id)
            storage.mark_storage(conn, merged["container_id"])
        if values:
            values["updated_at"] = db.utcnow_iso()
            conn.execute(
                f"UPDATE items SET {', '.join(f'{c} = ?' for c in values)} WHERE id = ?", [*values.values(), item_id]
            )
        if stickers is not None:
            _replace_stickers(conn, item_id, stickers)
    if values.get("market_hash_name"):
        images.ensure([values["market_hash_name"]])
    return jsonify(item=_get_item(item_id))


@bp.delete("/items/<int:item_id>")
def delete_item(item_id: int):
    """Remove an entry, or only ``?quantity=N`` of a stack. Containers: ``?contents=keep|delete``."""
    item = _get_item(item_id)
    quantity = _number(request.args.get("quantity"), "Quantity", 1, integer=True)
    contents = "delete" if request.args.get("contents") == "delete" else "keep"
    with db.connect() as conn:
        remaining = storage.remove(conn, item, quantity, contents)
    return jsonify(deleted=remaining == 0, remaining=remaining)


@bp.post("/items/<int:item_id>/move")
def move_item(item_id: int):
    """Move a whole stack, or ``quantity`` of it, into a container (``container_id: null`` = main inventory)."""
    item = _get_item(item_id)
    data = _body()
    container_id = _number(data.get("container_id"), "Container", 1, integer=True)
    quantity = _number(data.get("quantity"), "Quantity", 1, integer=True)
    with db.connect() as conn:
        new_id = storage.move(conn, item, container_id, quantity)
    return jsonify(item=_get_item(new_id))


@bp.get("/items/<int:item_id>/comparables")
def item_comparables(item_id: int):
    """Real CSFloat listings and recent sales of the same skin with the same valuable stickers."""
    _get_item(item_id)
    valuator = Valuator()
    frame = portfolio.build_frame(valuator, "total", with_changes=False)
    record = portfolio.to_records(frame[frame["id"] == item_id])[0]
    result = comparables.find_comparables(record, valuator, refresh=request.args.get("refresh") == "1")
    # Where the stickers outweigh the skin, this evidence becomes the item's value straight away.
    applied = False
    if settings.get("sticker_evidence", True) and comparables.needs_evidence(record, valuator):
        applied = comparables.save_evidence(record, result)
    return jsonify({**result, "applied": applied})


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------
@bp.get("/containers")
def list_containers():
    """Storage containers with fill level, value and a preview of their contents."""
    valuator = Valuator()
    frame = portfolio.build_frame(valuator, "total", with_changes=False)
    return jsonify(currency=valuator.base, **portfolio.storage_overview(frame, storage.CAPACITY))


@bp.get("/containers/<int:container_id>")
def open_container(container_id: int):
    item = _get_item(container_id)
    if item["category"] != "container":
        raise ValueError("That item is not a container.")
    if item["container_id"] is not None:
        raise ValueError("This container is stored inside another container, so it can't hold items itself.")
    valuator = Valuator()
    frame = portfolio.build_frame(valuator, "total")
    container = portfolio.to_records(frame[frame["id"] == container_id])[0]
    contents = portfolio.apply_filters(
        frame, container_id=container_id, sort=request.args.get("sort", "value"), order=request.args.get("order", "desc")
    )
    return jsonify(container=container, contents=portfolio.to_records(contents), currency=valuator.base,
                   capacity=storage.CAPACITY)


@bp.post("/containers/<int:container_id>/items")
def add_to_container(container_id: int):
    """Add several items at once, each with a quantity. Matching entries stack (7 + 21 = 28)."""
    _get_item(container_id)
    entries = _body().get("items")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Add at least one item.")
    if len(entries) > 100:
        raise ValueError("Add at most 100 different items at a time.")
    rows = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Row {number} is not valid.")
        name = _text(entry.get("market_hash_name"), f"Item name (row {number})", 200, required=True)
        quantity = _number(entry.get("quantity", 1), f"Quantity (row {number})", 1, storage.CAPACITY, integer=True) or 1
        rows.append((name, quantity))
    now = db.utcnow_iso()
    stacked = 0
    with db.connect() as conn:
        storage.check_placement(conn, None, {"source": "manual"}, container_id)
        storage.check_capacity(conn, container_id, sum(q for _, q in rows))
        storage.mark_storage(conn, container_id)
        for name, quantity in rows:
            values = {"source": "manual", "market_hash_name": name, "name": name, "category": categorize(name),
                      "exterior": exterior_from_name(name), "quantity": quantity, "container_id": container_id,
                      "marketable": 1, "tradable": 1, "created_at": now, "updated_at": now}
            stacked += storage.add_item(conn, values, [])[1]
        used = storage.contents_count(conn, container_id)
    images.ensure(name for name, _ in rows)
    return jsonify(added=len(rows) - stacked, stacked=stacked, contents_count=used, capacity=storage.CAPACITY), 201


@bp.delete("/containers/<int:container_id>")
def delete_container(container_id: int):
    """Delete a manual container (``?contents=keep`` moves its items out, ``delete`` removes them).
    Steam containers can't be deleted, so they are emptied instead."""
    container = _get_item(container_id)
    if container["category"] != "container":
        raise ValueError("That item is not a container.")
    contents = "delete" if request.args.get("contents") == "delete" else "keep"
    with db.connect() as conn:
        if container["source"] == "steam":
            emptied = storage.clear_contents(conn, container_id, contents)
            if container["market_hash_name"] != storage.STORAGE_UNIT:
                conn.execute("UPDATE items SET is_storage = 0 WHERE id = ?", (container_id,))
            return jsonify(deleted=False, emptied=emptied)
        storage.remove(conn, container, None, contents)
    return jsonify(deleted=True)


# ---------------------------------------------------------------------------
# Market comparison & manual quotes
# ---------------------------------------------------------------------------
@bp.get("/market-comparison")
def market_comparison():
    return jsonify(portfolio.market_comparison(Valuator(), _scope()))


@bp.get("/quotes")
def list_quotes():
    """Every current quote for one item (normalised to the base currency), plus a line for each
    enabled market without a price explaining why, and the CSFloat sticker match if there is one."""
    name = _text(request.args.get("name"), "name", 300, required=True)
    item_id = _number(request.args.get("item"), "item", 1, integer=True)
    valuator = Valuator()
    quotes = valuator.quotes[valuator.quotes["market_hash_name"] == name].sort_values("price_base")
    present = set(quotes["market"])
    checked = {r["market"] for r in db.query("SELECT market FROM price_checks WHERE market_hash_name = ?", (name,))}
    attempts = settings.get("market_attempts") or {}
    missing = []
    for key in valuator.prefs["enabled_markets"]:
        if key in present:
            continue
        source = SOURCES[key]
        usable, reason = source.available()
        if not usable:
            status = f"Not used: {reason}"
        elif key in checked:
            status = "Not listed there right now"
        elif key == "steam" and attempts.get(key):
            status = "Not fetched yet - Steam has been rate-limiting this computer"
        else:
            status = "Not fetched yet - press Refresh prices"
        missing.append({"market": key, "label": source.label, "status": status})
    match = None
    if item_id:
        found = evidence.load_all().get(item_id)
        if found:
            match = {**found, "value": valuator.fx.convert(found["value"], found["currency"], valuator.base)
                     if found["value"] else None}
    rows = [
        {
            "market": q.market,
            "label": market_label(q.market),
            "manual": bool(q.is_manual),
            "price": float(q.price),
            "currency": q.currency,
            "price_base": float(q.price_base),
            "volume": int(q.volume) if q.volume == q.volume and q.volume is not None else None,
            "url": q.url,
            "fetched_at": q.fetched_at,
            "net": valuator.net(q.market, float(q.price_base)),
            "cash_out": valuator.cash_out(q.market),
            "fee_note": fees.profile(q.market, valuator.fee_overrides)["note"],
        }
        for q in quotes.itertuples()
    ]
    return jsonify(currency=valuator.base, strategy=valuator.strategy, valuation=valuator.price(name), quotes=rows,
                   missing=missing, sticker_match=match)


@bp.post("/quotes")
def add_quote():
    data = _body()
    name = _text(data.get("market_hash_name"), "Item name", 200, required=True)
    label = re.sub(r"[^\w .()\-]", "", str(data.get("market_label") or "")).strip()[:40]
    if not label:
        raise ValueError("Market name is required (e.g. Buff163)")
    price = _number(data.get("price"), "Price", 0.0001)
    if price is None:
        raise ValueError("Price is required")
    code = _currency(data.get("currency"), "Currency") or settings.get("base_currency")
    if code not in FxConverter.load().currencies():
        raise ValueError(f"No exchange rate is available for {code}. Refresh prices first to load rates.")
    save_quotes(MANUAL_PREFIX + label, [Quote(name, price, code)], manual=True)
    return jsonify(ok=True), 201


@bp.delete("/quotes")
def delete_quote():
    data = _body()
    market = str(data.get("market") or "")
    if not market.startswith(MANUAL_PREFIX):
        raise ValueError("Only manual quotes can be deleted.")
    db.execute("DELETE FROM market_prices WHERE market = ? AND market_hash_name = ?", (market, data.get("market_hash_name")))
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# History & charts
# ---------------------------------------------------------------------------
def _range() -> str:
    key = request.args.get("range", "90d")
    if key not in history.RANGES:
        raise ValueError(f"range must be one of: {', '.join(history.RANGES)}")
    return key


@bp.get("/history/portfolio")
def portfolio_history():
    prefs = settings.get_all()
    return jsonify(history.portfolio_history(_scope(), _range(), FxConverter.load(), prefs["base_currency"]))


@bp.get("/history/item")
def item_history():
    name = _text(request.args.get("name"), "name", 300, required=True)
    return jsonify(history.item_history(name, _range(), Valuator()))


# ---------------------------------------------------------------------------
# Autocomplete & export
# ---------------------------------------------------------------------------
@bp.get("/names")
def names():
    """Autocomplete over every known item - not just ones currently for sale (see catalogue.py)."""
    query = _text(request.args.get("q"), "q", 200) or ""
    if len(query) < 2:
        return jsonify(names=[])
    owned = [r["market_hash_name"] for r in db.query("SELECT DISTINCT market_hash_name FROM items")]
    owned += [applied_hash_name(r["kind"], r["name"]) for r in db.query("SELECT DISTINCT kind, name FROM item_stickers")]
    prefix = APPLIED_KINDS.get(request.args.get("kind") or "", "")
    return jsonify(names=catalogue.search(query, owned=owned, prefix=prefix))


@bp.get("/export.csv")
def export_csv():
    scope = _scope()
    valuator = Valuator()
    frame = portfolio.build_frame(valuator, scope)
    if frame.empty:
        csv_text = "No items\n"
    else:
        filtered = portfolio.apply_filters(frame, categories=["weapon_skin", "sticker", "sticker_slab", "container",
                                                               "other", "in_container"])
        columns = ["name", "market_hash_name", "source", "category", "exterior", "float_value", "paint_seed",
                   "name_tag", "quantity", "container_name", "unit_price", "sticker_premium", "unit_value",
                   "total_value", "change_24h", "change_7w", "best_market", "best_price", "purchase_value", "profit"]
        out = filtered[columns].copy()
        out["sticker_names"] = filtered["stickers"].map(lambda s: "; ".join(a["name"] for a in s))
        buffer = io.StringIO()
        out.round(4).to_csv(buffer, index=False)
        csv_text = "﻿" + buffer.getvalue()  # BOM so Excel reads ★ and ™ correctly
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=cs2-{scope}-{history.today()}-{valuator.base}.csv"},
    )
