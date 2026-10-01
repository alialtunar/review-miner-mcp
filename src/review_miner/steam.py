"""Steam: public store endpoints. No key needed."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from .http import SourceError, get_json
from .models import App, Review

SOURCE = "Steam"
PAGE_SIZE = 100

ReviewType = Literal["all", "positive", "negative"]

CHARTS = ("top_sellers", "most_played", "new_releases")
WEB_API = "https://api.steampowered.com"
_GAME = 0  # Steam Web API item types: 0 game, 4 DLC, 10 hardware


def _price(cents: Any, currency: str = "USD") -> str:
    if cents in (None, ""):
        return ""
    try:
        cents = int(cents)
    except (TypeError, ValueError):
        return ""
    return "Free" if cents == 0 else f"{cents / 100:.2f} {currency}"


async def search_games(query: str, country: str, limit: int) -> list[App]:
    data = await get_json(
        "https://store.steampowered.com/api/storesearch/",
        {"term": query, "l": "english", "cc": country.upper()},
        source=SOURCE,
    )
    apps = []
    for item in data.get("items", [])[:limit]:
        price = item.get("price") or {}
        apps.append(App(
            store="steam",
            app_id=str(item.get("id", "")),
            name=item.get("name", ""),
            price=_price(price.get("final"), price.get("currency", "USD")) if price else "Free",
        ))
    return apps


async def top_games(country: str, list_name: str, limit: int) -> list[App]:
    """Ranked games from Steam's charts. DLC, hardware and bundles are dropped.

    top_sellers: weekly top sellers by revenue (up to 100, official Web API).
    most_played: most concurrent players (up to 100, official Web API).
    new_releases: the store front page's new releases (~30).
    """
    if list_name not in CHARTS:
        raise SourceError(f"Steam chart must be one of {', '.join(CHARTS)}.")
    if list_name == "most_played":
        return await _most_played(country, limit)
    if list_name == "top_sellers":
        try:
            return await _weekly_top_sellers(country, limit)
        except SourceError:
            pass  # the Web API is down or changed: fall back to the store front page
    return await _featured(country, list_name, limit)


def _web_api_params(request: dict[str, Any]) -> dict[str, str]:
    return {"input_json": json.dumps(request, separators=(",", ":"))}


def _context(country: str) -> dict[str, str]:
    return {"language": "english", "country_code": country.upper()}


def _app_from_item(item: dict[str, Any], rank: int) -> App:
    price = "Free" if item.get("is_free") else (item.get("best_purchase_option") or {}).get("formatted_final_price", "")
    return App(store="steam", app_id=str(item.get("appid", "")), name=item.get("name", ""), price=price, rank=rank)


def _ranked_games(items: list[dict[str, Any]], limit: int) -> list[App]:
    apps: list[App] = []
    for item in items:
        if item.get("type", _GAME) != _GAME or not item.get("name") or item.get("visible") is False:
            continue
        apps.append(_app_from_item(item, len(apps) + 1))
        if len(apps) >= limit:
            break
    return apps


async def _weekly_top_sellers(country: str, limit: int) -> list[App]:
    data = await get_json(
        f"{WEB_API}/IStoreTopSellersService/GetWeeklyTopSellers/v1/",
        _web_api_params({"country_code": country.upper(), "context": _context(country),
                         "data_request": {}, "page_count": min(100, limit + 15)}),
        source=SOURCE,
    )
    ranks = data.get("response", {}).get("ranks", [])
    if not ranks:
        raise SourceError("Steam's weekly top sellers came back empty.")
    return _ranked_games([r.get("item", {}) for r in ranks], limit)


async def _most_played(country: str, limit: int) -> list[App]:
    data = await get_json(f"{WEB_API}/ISteamChartsService/GetMostPlayedGames/v1/", source=SOURCE)
    appids = [r["appid"] for r in data.get("response", {}).get("ranks", []) if r.get("appid")]
    if not appids:
        raise SourceError("Steam's most played chart came back empty. Try chart='top_sellers'.")
    appids = appids[: min(100, limit + 15)]
    items = await get_json(
        f"{WEB_API}/IStoreBrowseService/GetItems/v1/",
        _web_api_params({"ids": [{"appid": a} for a in appids], "context": _context(country),
                         "data_request": {}}),
        source=SOURCE,
    )
    by_id = {i.get("appid"): i for i in items.get("response", {}).get("store_items", [])}
    return _ranked_games([by_id[a] for a in appids if a in by_id], limit)


async def _featured(country: str, list_name: str, limit: int) -> list[App]:
    """The store front page lists (~10 top sellers, ~30 new releases). Needs appdetails to drop non-games."""
    data = await get_json(
        "https://store.steampowered.com/api/featuredcategories",
        {"cc": country.upper(), "l": "english"},
        source=SOURCE,
    )
    items = data.get(list_name, {}).get("items", [])
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        app_id = str(item.get("id", ""))
        if not app_id or app_id in seen or item.get("type", 0) != 0:  # type 1 = package/bundle
            continue
        seen.add(app_id)
        candidates.append(item)
    basics = await asyncio.gather(*(app_basic(str(i["id"])) for i in candidates))
    apps: list[App] = []
    for item, basic in zip(candidates, basics):
        if basic and basic.get("type") not in (None, "game"):  # drop hardware, software, dlc
            continue
        apps.append(App(
            store="steam",
            app_id=str(item["id"]),
            name=item.get("name", ""),
            price=_price(item.get("final_price"), item.get("currency", "USD")),
            rank=len(apps) + 1,
        ))
        if len(apps) >= limit:
            break
    return apps


async def fetch_reviews(
    app_id: str, max_reviews: int, review_type: ReviewType = "all", language: str = "english"
) -> tuple[list[Review], App | None]:
    """Most recent reviews first. Returns (reviews, app summary with overall score)."""
    if not app_id.isdigit():
        raise SourceError("Steam app_id must be numeric (e.g. '1086940'). Use review_search_apps with store='steam'.")
    summary = await _summary(app_id, language)
    reviews: list[Review] = []
    cursor = "*"
    seen_cursors: set[str] = set()
    while len(reviews) < max_reviews:
        data = await get_json(
            f"https://store.steampowered.com/appreviews/{app_id}",
            {
                "json": 1, "filter": "recent", "language": language,
                "review_type": review_type, "purchase_type": "all",
                "num_per_page": min(PAGE_SIZE, max_reviews - len(reviews)), "cursor": cursor,
            },
            source=SOURCE,
        )
        if not data.get("success"):
            raise SourceError(f"Steam could not return reviews for app {app_id}. Check the ID.")
        batch = data.get("reviews", [])
        for r in batch:
            author = r.get("author", {}) or {}
            minutes = author.get("playtime_at_review") or author.get("playtime_forever")
            reviews.append(Review(
                store="steam",
                app_id=app_id,
                text=(r.get("review") or "").strip(),
                recommended=bool(r.get("voted_up")),
                date=_iso(r.get("timestamp_created")),
                helpful_votes=int(r.get("votes_up") or 0),
                playtime_hours=round(minutes / 60, 1) if minutes else None,
            ))
        cursor = data.get("cursor") or ""
        if not batch or not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    return reviews[:max_reviews], summary


async def _summary(app_id: str, language: str) -> App:
    """Overall score. Steam only returns totals when review_type=all, so ask separately."""
    data = await get_json(
        f"https://store.steampowered.com/appreviews/{app_id}",
        {"json": 1, "filter": "all", "language": language, "review_type": "all",
         "purchase_type": "all", "num_per_page": 0},
        source=SOURCE,
    )
    if not data.get("success"):
        raise SourceError(f"Steam could not return reviews for app {app_id}. Check the ID.")
    q = data.get("query_summary", {})
    pos, neg = q.get("total_positive") or 0, q.get("total_negative") or 0
    return App(
        store="steam", app_id=app_id, name="",
        rating=round(100 * pos / (pos + neg), 1) if pos + neg else None,
        rating_count=q.get("total_reviews"),
        review_summary=q.get("review_score_desc"),
    )


async def app_basic(app_id: str) -> dict[str, Any] | None:
    """Name and type ('game', 'hardware', 'dlc'...). None if Steam has no page for it."""
    try:
        data = await get_json(
            "https://store.steampowered.com/api/appdetails",
            {"appids": app_id, "filters": "basic"}, source=SOURCE,
        )
    except SourceError:
        return None
    entry = data.get(app_id, {}) if isinstance(data, dict) else {}
    return entry.get("data") if entry.get("success") else None


async def app_name(app_id: str) -> str:
    basic = await app_basic(app_id)
    return (basic or {}).get("name", "")


def _iso(ts: Any) -> str | None:
    if not ts:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
