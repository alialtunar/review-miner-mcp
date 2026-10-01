"""Mock Apple + Steam endpoints with payloads shaped like the real ones."""

from __future__ import annotations

import httpx
import pytest

from review_miner import http


def _l(v):
    return {"label": str(v)}


def apple_review(i: int, stars: int, text: str, version: str, day: int, votes: int = 0):
    return {
        "author": {"name": _l(f"user{i}")},
        "im:version": _l(version),
        "im:rating": _l(stars),
        "id": _l(1000 + i),
        "title": _l(["Crashes", "Too pricey", "Sync broken", "Meh", "Disappointed"][i % 5] if stars <= 2 else "Great"),
        "content": {"label": text, "attributes": {"type": "text"}},
        "im:voteCount": _l(votes),
        "updated": _l(f"2026-09-{day:02d}T10:00:00-07:00"),
    }


APPLE_TEXTS_NEG = [
    "The subscription price is too expensive and it keeps crashing after the update",
    "Keeps crashing on startup. Subscription price doubled, cancel subscription is hidden",
    "Ads everywhere and the sync with apple watch is broken",
    "Apple watch sync broken again, lost my workout data",
    "Too expensive subscription price for what it does",
]
APPLE_TEXTS_POS = ["Love it, great workouts", "Best fitness app I have used", "Great coach and plans"]


def apple_reviews_page(page: int):
    if page > 2:
        return {"feed": {"author": {}, "entry": []}}
    entries = []
    for j in range(50):
        i = (page - 1) * 50 + j
        if i % 2 == 0:
            e = apple_review(i, 1 if i % 4 == 0 else 2, APPLE_TEXTS_NEG[i % 5], "7.2" if i < 60 else "7.1", 28 - i // 10, votes=i % 7)
        else:
            e = apple_review(i, 5, APPLE_TEXTS_POS[i % 3], "7.2" if i < 60 else "7.1", 28 - i // 10)
        entries.append(e)
    return {"feed": {"entry": entries}}


APPLE_TOP = {"feed": {"entry": [
    {"im:name": _l("FitNow"), "im:artist": _l("FitNow Inc."), "im:price": _l("Get"),
     "id": {"label": "https://apps.apple.com/app/id111", "attributes": {"im:id": "111"}},
     "category": {"attributes": {"im:id": "6013", "label": "Health & Fitness"}}},
    {"im:name": _l("RunPro"), "im:artist": _l("RunPro Ltd."), "im:price": _l("Get"),
     "id": {"label": "https://apps.apple.com/app/id222", "attributes": {"im:id": "222"}},
     "category": {"attributes": {"im:id": "6013", "label": "Health & Fitness"}}},
]}}

APPLE_LOOKUP = {"resultCount": 1, "results": [{
    "trackId": 111, "trackName": "FitNow", "sellerName": "FitNow Inc.", "primaryGenreName": "Health & Fitness",
    "formattedPrice": "Free", "averageUserRating": 4.61234, "userRatingCount": 120345,
    "version": "7.2", "currentVersionReleaseDate": "2026-09-20T07:00:00Z"}]}


STEAM_SUMMARY = {"num_reviews": 0, "review_score": 5, "review_score_desc": "Mixed",
                 "total_positive": 600, "total_negative": 400, "total_reviews": 1000}


def steam_page(cursor: str, review_type: str, per_page: str):
    if per_page == "0":  # real Steam: totals only when review_type=all
        return {"success": 1, "cursor": "*", "reviews": [],
                "query_summary": STEAM_SUMMARY if review_type == "all" else {"num_reviews": 0}}
    if cursor == "*":
        reviews = [{
            "recommendationid": str(k), "review": t, "voted_up": up, "votes_up": k,
            "timestamp_created": 1790000000 + k, "author": {"playtime_at_review": 60 * (k + 1)},
        } for k, (t, up) in enumerate([
            ("Performance is terrible, constant stutter and crashes", False),
            ("Great story, loved it", True),
            ("Crashes every hour, performance issues on my GPU", False),
            ("Microtransactions ruin it, pay to win", False),
        ])]
        if review_type == "negative":
            reviews = [r for r in reviews if not r["voted_up"]]
        return {"success": 1, "cursor": "AoJ1", "reviews": reviews,
                "query_summary": {"num_reviews": len(reviews)}}
    return {"success": 1, "cursor": "AoJ1", "reviews": [], "query_summary": {"num_reviews": 0}}


def handler(request: httpx.Request) -> httpx.Response:
    url, p = str(request.url), request.url.params
    if "customerreviews" in url:
        page = int(url.split("page=")[1].split("/")[0])
        app_id = url.split("id=")[1].split("/")[0]
        if app_id == "404404":
            return httpx.Response(404)
        return httpx.Response(200, json=apple_reviews_page(page))
    if "/rss/top" in url:
        return httpx.Response(200, json=APPLE_TOP)
    if url.startswith("https://itunes.apple.com/lookup"):
        return httpx.Response(200, json=APPLE_LOOKUP)
    if url.startswith("https://itunes.apple.com/search"):
        return httpx.Response(200, json=APPLE_LOOKUP)
    if "appreviews" in url:
        return httpx.Response(200, json=steam_page(p.get("cursor", "*"), p.get("review_type", "all"), p.get("num_per_page", "20")))
    if "appdetails" in url:
        app = p.get("appids")
        known = {"1091500": ("Cyberpunk 2077", "game"), "730": ("Counter-Strike 2", "game"),
                 "4165890": ("Steam Frame", "hardware")}
        if app not in known:
            return httpx.Response(200, json={app: {"success": False}})
        name, kind = known[app]
        return httpx.Response(200, json={app: {"success": True, "data": {"name": name, "type": kind}}})
    if "storesearch" in url:
        return httpx.Response(200, json={"total": 1, "items": [
            {"id": 1091500, "name": "Cyberpunk 2077", "price": {"currency": "USD", "final": 5999}}]})
    if "featuredcategories" in url:
        return httpx.Response(200, json={"top_sellers": {"items": [
            {"id": 4165890, "name": "Steam Frame", "type": 0, "final_price": 105900},
            {"id": 1675062, "name": "Founder's Pack", "type": 1, "final_price": 4999},
            {"id": 730, "name": "Counter-Strike 2", "final_price": 0},
            {"id": 730, "name": "Counter-Strike 2", "final_price": 0},
            {"id": 1091500, "name": "Cyberpunk 2077", "final_price": 5999, "currency": "USD"}]}})
    return httpx.Response(500)


@pytest.fixture(autouse=True)
def mock_network():
    calls: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    http.set_transport(httpx.MockTransport(recording))
    yield calls
    http.set_transport(None)
