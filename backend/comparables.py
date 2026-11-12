"""Market evidence for items with applied stickers, from CSFloat.

A formula can't know what collectors pay for a particular sticker combination, so
this looks at real CSFloat data for the same skin:

  1. recent sales that carried the same valuable stickers,
  2. current listings with the same valuable stickers (asking prices),
  3. the same two checks for just the most valuable sticker,

and suggests a value from the strongest evidence available. Comparables that also
carry other valuable stickers are set aside, because those would inflate the price.

Uses CSFloat's public schema (sticker IDs, no key) and its listings / sales history
(needs CSFLOAT_API_KEY). Read-only; results are cached for an hour.
"""
from __future__ import annotations

import hashlib
import statistics
from typing import Any
from urllib.parse import quote

from config import Config

from . import cache, catalogue, evidence
from .classify import applied_hash_name
from .http_client import ApiError, AuthError, RateLimitError
from .markets import SOURCES

LISTINGS_URL = "https://csfloat.com/api/v1/listings"
SALES_URL = "https://csfloat.com/api/v1/history/{name}/sales"
ITEM_URL = "https://csfloat.com/item/{id}"
RESULT_CACHE_SECONDS = 3600
MAX_KEY_STICKERS = 3
ASKING_DISCOUNT = 0.85   # listings are asking prices; sales usually clear somewhat lower
EXTRA_LIMIT = 0.25       # other stickers may add at most 25 % of a comparable's price


# ---------------------------------------------------------------------------
# Sticker IDs
# ---------------------------------------------------------------------------
def _sticker_schema() -> dict[str, str]:
    """CSFloat sticker/patch ID (as text) -> market name, from the public schema (cached a week)."""
    cached = cache.get_cached(catalogue.STICKERS_KEY, catalogue.SCHEMA_CACHE_SECONDS)
    if cached is not None:
        return cached
    try:
        return catalogue.refresh_schema()["stickers"]
    except ApiError as exc:
        stale = cache.get_stale(catalogue.STICKERS_KEY)
        if stale is not None:
            return stale
        raise ApiError(f"Couldn't load CSFloat's sticker list ({exc})") from None


def sticker_names() -> dict[int, str]:
    return {int(k): v for k, v in _sticker_schema().items()}


def sticker_ids() -> dict[str, int]:
    """Lower-cased market name -> CSFloat sticker/patch ID."""
    return {name.lower(): sticker_id for sticker_id, name in sticker_names().items()}


def _reference_prices(valuator) -> dict[str, float]:
    """Cheapest known price (base currency) for any item name, from the bulk price lists
    (fetched if they aren't cached yet). Used to judge what *other* stickers on a comparable are worth."""
    prices = dict(valuator.lowest)
    for key in ("marketcsgo", "waxpeer", "skinport"):
        source = SOURCES[key]
        try:
            rows = source.load_rows()
        except ApiError:
            rows = cache.get_stale(source._cache_key()) or []
        for row in rows:
            value = valuator.fx.convert(row[1], source.currency, valuator.base)
            if value is not None and (row[0] not in prices or value < prices[row[0]]):
                prices[row[0]] = value
    return prices


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def find_comparables(item: dict[str, Any], valuator, refresh: bool = False) -> dict[str, Any]:
    source = SOURCES["csfloat"]
    usable, reason = source.available()
    if not usable:
        raise ValueError(f"Comparing on CSFloat {reason.replace('needs', 'needs a')}.")
    if not item.get("stickers"):
        raise ValueError("This item has no applied stickers to compare.")

    ids = sticker_ids()
    key = _key_stickers(item["stickers"], ids, valuator)
    if not key:
        raise ValueError("None of this item's stickers are valuable enough (or known to CSFloat) to compare.")

    signature = hashlib.sha1(f"{item['market_hash_name']}|{[k['id'] for k in key]}".encode()).hexdigest()[:16]
    cache_key = f"comparables:{signature}"
    if not refresh:
        cached = cache.get_cached(cache_key, RESULT_CACHE_SECONDS)
        if cached is not None:
            return {**cached, "cached": True}

    name = item["market_hash_name"]
    headers = {"Authorization": Config.CSFLOAT_API_KEY}
    key_ids = [k["id"] for k in key]
    unique_ids = list(dict.fromkeys(key_ids))

    def listings(sticker_ids_: list[int]) -> list[dict]:
        resp = source.client.get(LISTINGS_URL, headers=headers, params={
            "market_hash_name": name, "sort_by": "lowest_price", "type": "buy_now", "limit": 20,
            "stickers": _sticker_filter(sticker_ids_),
        })
        return _rows(resp)

    combo_listings = listings(unique_ids)
    top_listings = listings(unique_ids[:1]) if len(unique_ids) > 1 else []
    sales = _rows(source.client.get(SALES_URL.format(name=quote(name, safe="")), headers=headers))

    names = sticker_names()
    refs = _reference_prices(valuator)
    convert = lambda usd: valuator.fx.convert(usd, "USD", valuator.base)  # noqa: E731

    def build(rows: list[dict], needed: list[int], sold: bool) -> list[dict]:
        out = []
        for row in rows:
            entry = _comparable(row, needed, key_ids, names, refs, convert, sold)
            if entry:
                out.append(entry)
        return out

    top_name = key[0]["name"]
    groups = [
        {"id": "combo_sales", "title": "Recent sales with the same stickers", "sold": True,
         "rows": build(sales, key_ids, True)},
        {"id": "combo_listings", "title": "Listed now with the same stickers", "sold": False,
         "rows": build(combo_listings, key_ids, False)},
    ]
    if len(unique_ids) > 1:
        groups += [
            {"id": "top_sales", "title": f"Recent sales with {top_name}", "sold": True,
             "rows": build(sales, key_ids[:1], True)},
            {"id": "top_listings", "title": f"Listed now with {top_name}", "sold": False,
             "rows": build(top_listings, key_ids[:1], False)},
        ]

    plain = valuator.price(name)
    result = {
        "market_hash_name": name,
        "currency": valuator.base,
        "key_stickers": key,
        "plain_price": plain,
        "groups": groups,
        "suggestion": _suggest(groups, plain, key),
        "cached": False,
    }
    cache.set_cached(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Automatic evidence during price refreshes
# ---------------------------------------------------------------------------
MAX_EVIDENCE_ITEMS = 25   # per refresh, to stay well inside CSFloat's rate limits


def needs_evidence(record: dict[str, Any], valuator) -> bool:
    """Items whose stickers are worth more than the skin itself: there the sticker formula is
    unreliable, so real CSFloat listings/sales with the same stickers are used instead."""
    if not record.get("stickers") or record.get("price_source") == "custom":
        return False
    plain = valuator.price(record["market_hash_name"]) or 0
    floor = valuator.fx.convert(10.0, "USD", valuator.base) or 10.0
    return (record.get("sticker_loose_total") or 0) >= max(plain, floor)


def save_evidence(record: dict[str, Any], result: dict[str, Any] | None, reason: str = "") -> bool:
    sig = evidence.signature(record["stickers"])
    suggestion = (result or {}).get("suggestion")
    if suggestion:
        evidence.save(record["id"], sig, suggestion["value"], result["currency"], suggestion["basis"], suggestion["notes"])
        return True
    evidence.save(record["id"], sig, None, None, reason or "No clean comparables on CSFloat right now.", [])
    return False


def refresh_evidence(job=None) -> dict[str, int]:
    """Gather CSFloat evidence for every item that needs it (re-checked once a day)."""
    from . import portfolio
    from . import settings as app_settings
    from .valuation import Valuator

    if not app_settings.get("sticker_evidence", True) or not SOURCES["csfloat"].available()[0]:
        return {"checked": 0, "valued": 0}
    valuator = Valuator()
    frame = portfolio.build_frame(valuator, "total", with_changes=False)
    if frame.empty:
        return {"checked": 0, "valued": 0}
    existing = evidence.load_all()
    todo = [r for r in portfolio.to_records(frame)
            if needs_evidence(r, valuator) and evidence.due(existing.get(r["id"]), r["stickers"])][:MAX_EVIDENCE_ITEMS]
    valued = 0
    for index, record in enumerate(todo):
        if job and job.cancelled:
            break
        if job:
            job.update(message=f"CSFloat: comparing the stickers on {record['name']} ({index + 1}/{len(todo)})")
        try:
            valued += save_evidence(record, find_comparables(record, valuator))
        except ValueError as exc:
            save_evidence(record, None, str(exc))
        except (RateLimitError, AuthError) as exc:
            if job:
                job.warn(f"Sticker comparison stopped: {exc}")
            break
        except ApiError:
            continue
    return {"checked": len(todo), "valued": valued}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sticker_filter(ids: list[int]) -> str:
    return "[" + ",".join(f'{{"i":{i}}}' for i in ids) + "]"


def _rows(resp) -> list[dict]:
    if resp.status_code in (401, 403):
        raise ApiError("CSFloat rejected the API key. Check CSFLOAT_API_KEY in .env.", resp.status_code)
    if resp.status_code != 200:
        raise ApiError(f"CSFloat returned HTTP {resp.status_code}", resp.status_code)
    data = resp.json()
    return (data.get("data") if isinstance(data, dict) else data) or []


def _key_stickers(stickers: list[dict], ids: dict[str, int], valuator) -> list[dict]:
    """The stickers worth matching on: priced, known to CSFloat, and not trivially cheap
    next to the most valuable one (a cheap foil shouldn't narrow the search)."""
    floor = valuator.fx.convert(1.0, "USD", valuator.base) or 1.0
    candidates = []
    for s in stickers:
        if s.get("kind") not in ("sticker", "patch") or not s.get("loose_price"):
            continue
        sticker_id = ids.get(applied_hash_name(s["kind"], s["name"]).lower())
        if sticker_id is not None:
            candidates.append({"id": sticker_id, "name": s["name"], "loose_price": s["loose_price"],
                               "slot": s.get("slot")})
    if not candidates:
        return []
    candidates.sort(key=lambda c: -c["loose_price"])
    top = candidates[0]["loose_price"]
    return [c for c in candidates if c["loose_price"] >= max(floor, top * 0.05)][:MAX_KEY_STICKERS]


def _contains(found: list[int], needed: list[int]) -> bool:
    remaining = list(found)
    for sticker_id in needed:
        if sticker_id not in remaining:
            return False
        remaining.remove(sticker_id)
    return True


def _comparable(row: dict, needed: list[int], key_ids: list[int], names: dict[int, str],
                refs: dict[str, float], convert, sold: bool) -> dict | None:
    item = row.get("item") or {}
    stickers = item.get("stickers") or []
    found = [s.get("stickerId") for s in stickers]
    if not _contains(found, needed):
        return None
    price_usd = (row.get("price") or 0) / 100
    price = convert(price_usd)
    if not price:
        return None
    remaining_key = list(needed)
    extra, unpriced, chips = 0.0, 0, []
    for s in sorted(stickers, key=lambda s: s.get("slot") or 0):
        sticker_id = s.get("stickerId")
        name = names.get(sticker_id, f"sticker #{sticker_id}")
        is_key = sticker_id in remaining_key
        if is_key:
            remaining_key.remove(sticker_id)
        else:
            value = refs.get(name)
            if value is None:
                unpriced += 1
            else:
                extra += value
        chips.append({"name": _short(name), "key": is_key, "wear": round(float(s.get("wear") or 0), 3)})
    return {
        "price": price,
        "price_usd": price_usd,
        "float": item.get("float_value"),
        "stickers": chips,
        "extra_value": extra,
        "unpriced_extras": unpriced,
        # Usable as evidence only if its other stickers are known and add little.
        "clean": unpriced == 0 and extra <= max(price * EXTRA_LIMIT, 1.0),
        "date": row.get("sold_at") if sold else row.get("created_at"),
        "url": ITEM_URL.format(id=row.get("id")) if row.get("id") else None,
    }


def _short(name: str) -> str:
    for prefix in ("sticker | ", "patch | "):
        if name.lower().startswith(prefix):
            return name[len(prefix):]
    return name


def _suggest(groups: list[dict], plain: float | None, key: list[dict]) -> dict | None:
    by_id = {g["id"]: g for g in groups}
    tiers = [("combo_sales", "median", 1.0), ("combo_listings", "lowest", ASKING_DISCOUNT),
             ("top_sales", "median", 1.0), ("top_listings", "lowest", ASKING_DISCOUNT)]
    for group_id, how, factor in tiers:
        group = by_id.get(group_id)
        rows = [r for r in (group["rows"] if group else []) if r["clean"]]
        if not rows:
            continue
        prices = sorted(r["price"] for r in rows)
        evidence = (statistics.median(prices) if how == "median" else prices[0]) * factor
        value = max(evidence, plain or 0)
        note = []
        if how == "lowest":
            note.append("Listings are asking prices, so 15% has been taken off.")
        if group_id.startswith("top_") and plain:
            # The comparables only share the most valuable sticker. Work out what share of that
            # sticker's loose price buyers actually paid, and apply the same share to all of
            # this item's valuable stickers.
            rate = max(evidence - plain, 0) / key[0]["loose_price"]
            value = plain + rate * sum(k["loose_price"] for k in key)
            others = ", ".join(k["name"] for k in key[1:])
            note.append(f"These only share {key[0]['name']}. On them it added {rate:.1%} of its loose price, "
                        f"so the same share was applied to {others} too.")
        if len(rows) < 3:
            note.append(f"Only {len(rows)} comparable{'s' if len(rows) > 1 else ''} found, so treat this as a rough guide.")
        return {
            "value": round(value, 2),
            "basis": f"{len(rows)} usable {'sale' if how == 'median' else 'listing'}{'s' if len(rows) > 1 else ''} "
                     f"({group['title'][0].lower()}{group['title'][1:]})",
            "notes": note,
        }
    return None
