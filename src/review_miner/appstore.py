"""Apple App Store: public iTunes Search/Lookup API and RSS feeds. No key needed."""

from __future__ import annotations

import asyncio
from typing import Any

from .http import SourceError, get_json
from .models import App, Review

SOURCE = "Apple App Store"
MAX_REVIEW_PAGES = 10  # Apple's review RSS stops at 10 pages x 50 reviews

# Equivalent spellings of the most-recent review feed. Apple caches each URL path separately and
# some caches hold an empty feed for hours, so when one spelling is empty another is often full.
# All of these return identical entries when non-empty (verified 2026-10-01).
def _review_feed_spellings(limit: int = 16) -> tuple[str, ...]:
    sorts = [f"{k}={v}" for v in ("mostrecent", "mostRecent", "MostRecent", "MOSTRECENT")
             for k in ("sortby", "sortBy", "SortBy", "SORTBY")]
    spellings = ["sortby=mostrecent/json", "json", "sortBy=mostRecent/json", "limit=50/sortby=mostrecent/json",
                 "sortby=mostrecent/limit=50/json", "limit=50/sortBy=mostRecent/json", "limit=50/json"]
    for sort in sorts:
        spellings += [f"{sort}/json", f"limit=50/{sort}/json", f"{sort}/limit=50/json"]
    return tuple(dict.fromkeys(spellings))[:limit]


REVIEW_FEED_SPELLINGS = _review_feed_spellings()
SPELLINGS_PER_BATCH = 4  # matches the HTTP layer's concurrency limit

GENRES: dict[str, int] = {
    "books": 6018, "business": 6000, "developer-tools": 6026, "education": 6017,
    "entertainment": 6016, "finance": 6015, "food-drink": 6023, "games": 6014,
    "graphics-design": 6027, "health-fitness": 6013, "lifestyle": 6012,
    "medical": 6020, "music": 6011, "navigation": 6010, "news": 6009,
    "photo-video": 6008, "productivity": 6007, "reference": 6006,
    "shopping": 6024, "social-networking": 6005, "sports": 6004,
    "travel": 6003, "utilities": 6002, "weather": 6001,
}

CHARTS = {
    "free": "topfreeapplications",
    "paid": "toppaidapplications",
    "grossing": "topgrossingapplications",
}


def _label(node: Any) -> str:
    """Apple RSS wraps values as {"label": ...}. Unwrap safely."""
    if isinstance(node, dict):
        return str(node.get("label", "")).strip()
    return "" if node is None else str(node).strip()


def resolve_genre(category: str | None) -> int | None:
    if not category:
        return None
    key = category.strip().lower().replace(" & ", "-").replace("&", "-").replace(" ", "-")
    if key.isdigit():
        return int(key)
    if key in GENRES:
        return GENRES[key]
    raise SourceError(f"Unknown App Store category '{category}'. Use one of: {', '.join(sorted(GENRES))}.")


async def top_apps(category: str | None, country: str, chart: str, limit: int) -> list[App]:
    if chart not in CHARTS:
        raise SourceError(f"chart must be one of {list(CHARTS)}.")
    genre = resolve_genre(category)
    url = f"https://itunes.apple.com/{country}/rss/{CHARTS[chart]}/limit={limit}"
    if genre:
        url += f"/genre={genre}"
    data = await get_json(url + "/json", source=SOURCE)
    entries = data.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    apps = []
    for rank, e in enumerate(entries, start=1):
        apps.append(App(
            store="appstore",
            app_id=str(e.get("id", {}).get("attributes", {}).get("im:id", "")),
            name=_label(e.get("im:name")),
            developer=_label(e.get("im:artist")),
            category=e.get("category", {}).get("attributes", {}).get("label", ""),
            price=_label(e.get("im:price")),
            rank=rank,
        ))
    return apps


def _app_from_lookup(r: dict[str, Any]) -> App:
    return App(
        store="appstore",
        app_id=str(r.get("trackId", "")),
        name=r.get("trackName", ""),
        developer=r.get("sellerName") or r.get("artistName", ""),
        category=r.get("primaryGenreName", ""),
        price=r.get("formattedPrice", ""),
        rating=r.get("averageUserRating"),
        rating_count=r.get("userRatingCount"),
        version=r.get("version"),
        updated=(r.get("currentVersionReleaseDate") or "")[:10] or None,
    )


async def search_apps(query: str, country: str, limit: int) -> list[App]:
    data = await get_json(
        "https://itunes.apple.com/search",
        {"term": query, "entity": "software", "country": country, "limit": limit},
        source=SOURCE,
    )
    return [_app_from_lookup(r) for r in data.get("results", [])]


async def lookup_app(app_id: str, country: str) -> App | None:
    data = await get_json("https://itunes.apple.com/lookup", {"id": app_id, "country": country}, source=SOURCE)
    results = data.get("results", [])
    return _app_from_lookup(results[0]) if results else None


async def fetch_reviews(app_id: str, country: str, max_reviews: int) -> list[Review]:
    """Most recent reviews first. Apple caps this feed at ~500 reviews per country."""
    if not app_id.isdigit():
        raise SourceError("App Store app_id must be numeric (e.g. '389801252'). Use review_search_apps to find it.")
    reviews: list[Review] = []
    seen: set[str] = set()
    for page in range(1, MAX_REVIEW_PAGES + 1):
        page_reviews = await _fetch_review_page(app_id, country, page)
        if not page_reviews:
            break
        for r in page_reviews:
            if r.review_id not in seen:
                seen.add(r.review_id)
                reviews.append(r)
        if len(reviews) >= max_reviews:
            break
    if not reviews:
        raise SourceError(
            f"Apple's review feed came back empty for app {app_id} in the {country.upper()} store. "
            "Either the app has no written reviews there, or Apple's feed is temporarily empty "
            "(this happens). Retry in a few minutes or try another country such as 'us' or 'gb'."
        )
    return reviews[:max_reviews]


async def _fetch_review_page(app_id: str, country: str, page: int) -> list[Review]:
    """One page of 50 reviews, trying URL spellings 4 at a time until one is non-empty."""
    base = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}"
    for i in range(0, len(REVIEW_FEED_SPELLINGS), SPELLINGS_PER_BATCH):
        batch = REVIEW_FEED_SPELLINGS[i:i + SPELLINGS_PER_BATCH]
        for data in await asyncio.gather(*(get_json(f"{base}/{s}", source=SOURCE, cache_if=_has_entries) for s in batch)):
            entries = data.get("feed", {}).get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]
            page_reviews = [r for r in (_review_from_entry(e, app_id) for e in entries) if r]
            if page_reviews:
                return page_reviews
    return []


def _has_entries(data: object) -> bool:
    return isinstance(data, dict) and bool(data.get("feed", {}).get("entry"))


def _review_from_entry(e: dict[str, Any], app_id: str) -> Review | None:
    rating = _label(e.get("im:rating"))
    if not rating.isdigit():  # older feeds put an app-info entry first
        return None
    votes = _label(e.get("im:voteCount"))
    return Review(
        store="appstore",
        app_id=app_id,
        review_id=_label(e.get("id")),
        rating=int(rating),
        title=_label(e.get("title")),
        text=_label(e.get("content")),
        version=_label(e.get("im:version")) or None,
        date=_label(e.get("updated"))[:10] or None,
        helpful_votes=int(votes) if votes.isdigit() else 0,
    )
