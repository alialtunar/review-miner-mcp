import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from review_miner import appstore, steam
from review_miner.analysis import summarize, top_terms
from review_miner.http import SourceError
from review_miner.server import (
    mcp, review_compare_apps, review_fetch, review_search_apps, review_top_apps,
)


# ---- adapters ----

async def test_appstore_reviews_paginate_and_stop():
    reviews = await appstore.fetch_reviews("111", "us", 500)
    assert len(reviews) == 100          # 2 pages of 50, then an empty page stops the loop
    assert {r.rating for r in reviews} <= {1, 2, 5}
    assert reviews[0].version == "7.2" and reviews[0].date.startswith("2026-09")


async def test_appstore_reviews_respect_max():
    assert len(await appstore.fetch_reviews("111", "us", 30)) == 30


async def test_appstore_falls_back_to_other_url_spellings(mock_network):
    reviews = await appstore.fetch_reviews("333", "us", 500)
    assert len(reviews) == 100
    assert len({(r.date, r.text, r.rating, r.helpful_votes, r.title) for r in reviews}) > 1
    assert any("sortby=mostrecent/json" in u for u in mock_network)  # primary tried first


async def test_appstore_dedupes_reviews_across_spellings():
    reviews = await appstore.fetch_reviews("333", "us", 500)
    assert len({r.review_id for r in reviews}) == len(reviews)


async def test_appstore_empty_feed_is_explained_not_hidden():
    with pytest.raises(SourceError, match="empty"):
        await appstore.fetch_reviews("444", "us", 100)


async def test_compare_reports_empty_apple_feed_honestly():
    out = await review_compare_apps(apps=["appstore:444", "steam:1091500"])
    assert "no recent reviews" not in out
    assert "empty" in out


async def test_appstore_rejects_non_numeric_id():
    with pytest.raises(SourceError, match="numeric"):
        await appstore.fetch_reviews("duolingo", "us", 50)


def test_genre_resolution():
    assert appstore.resolve_genre("Health & Fitness") == 6013
    assert appstore.resolve_genre("6015") == 6015
    with pytest.raises(SourceError, match="Unknown"):
        appstore.resolve_genre("cats")


async def test_steam_reviews_and_summary():
    reviews, summary = await steam.fetch_reviews("1091500", 200)
    assert len(reviews) == 4
    assert summary.rating == 60.0 and summary.review_summary == "Mixed"
    assert reviews[0].playtime_hours == 1.0


async def test_steam_negative_fetch_still_has_overall_score():
    reviews, summary = await steam.fetch_reviews("1091500", 200, "negative")
    assert summary.rating == 60.0 and all(r.recommended is False for r in reviews)


async def test_steam_top_sellers_use_weekly_chart_without_dlc_or_hardware():
    apps = await steam.top_games("us", "top_sellers", 10)
    assert [a.name for a in apps] == ["EA SPORTS FC 27", "Counter-Strike 2", "Cyberpunk 2077"]
    assert [a.rank for a in apps] == [1, 2, 3]
    assert apps[0].price == "$69.99" and apps[1].price == "Free"


async def test_steam_most_played_resolves_names_and_drops_hardware():
    apps = await steam.top_games("us", "most_played", 10)
    assert [a.name for a in apps] == ["Counter-Strike 2", "Dota 2", "Cyberpunk 2077"]


async def test_steam_rejects_unknown_chart():
    with pytest.raises(SourceError, match="most_played"):
        await steam.top_games("us", "best_ever", 10)


async def test_steam_top_sellers_fall_back_to_store_page_when_web_api_fails():
    apps = await steam.top_games("zz", "top_sellers", 10)
    assert [a.app_id for a in apps] == ["730", "1091500"]
    assert apps[0].price == "Free"


# ---- analysis ----

async def test_summary_finds_real_complaints():
    reviews = await appstore.fetch_reviews("111", "us", 200)
    stats = summarize(reviews)
    assert stats["negative_share"] == "50%"
    terms = [t["term"] for t in stats["top_complaint_terms"]]
    assert "subscription price" in terms
    assert any("crashing" in t for t in terms)
    assert stats["rating_by_version"][0]["version"] == "7.2"
    assert "expensive keeps" not in terms and "price expensive" not in terms  # no cross-clause junk


def test_contrast_drops_words_everyone_uses():
    from review_miner.models import Review
    neg = [Review("appstore", "1", f"The story is ok but the servers crash constantly {i}", rating=1) for i in range(10)]
    pos = [Review("appstore", "1", f"The story is amazing and the world is beautiful {i}", rating=5) for i in range(10)]
    terms = [t["term"] for t in top_terms(neg, baseline=pos)]
    assert "story" not in terms
    assert any("crash" in t for t in terms)


def test_app_name_and_contractions_ignored():
    from review_miner.models import Review
    neg = [Review("appstore", "1", f"Duolingo don't work, streak freeze broken {i}", rating=1) for i in range(5)]
    terms = [t["term"] for t in top_terms(neg, ignore={"duolingo"})]
    assert "duolingo" not in terms and "don" not in terms
    assert "streak freeze" in terms


def test_top_terms_empty():
    assert top_terms([]) == []


# ---- tools ----

async def test_tool_fetch_markdown():
    out = await review_fetch("appstore:111", country="us", max_reviews=100, sample_size=5)
    assert "FitNow" in out and "4.61★" in out
    assert "Top complaint terms" in out
    assert out.count("\n- 1★") + out.count("\n- 2★") == 5


async def test_tool_fetch_json_steam():
    data = json.loads(await review_fetch("1091500", store="steam", response_format="json"))
    assert data["app"]["rating"] == 60.0
    assert len(data["samples"]) == 3
    assert all(s["recommended"] is False for s in data["samples"])  # only='negative' by default


async def test_tool_compare_mixed_stores_and_errors():
    out = await review_compare_apps(["appstore:111", "steam:1091500", "appstore:404404"], max_reviews_per_app=100)
    assert "| FitNow |" in out
    assert "60.0% 👍" in out and "| Cyberpunk 2077 |" in out
    assert "404" in out  # bad ID reported in its row, other apps still returned


async def test_tool_top_and_search():
    assert "FitNow" in await review_top_apps(category="health-fitness")
    assert "Cyberpunk 2077" in await review_search_apps("cyberpunk", store="steam")
    assert "Unknown App Store category" in await review_top_apps(category="cats")


async def test_cache_avoids_duplicate_calls(mock_network):
    await review_fetch("appstore:111", max_reviews=50)
    n = len(mock_network)
    await review_fetch("appstore:111", max_reviews=50)
    assert len(mock_network) == n


# ---- MCP protocol end-to-end ----

async def test_protocol_list_and_call():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"review_top_apps", "review_search_apps", "review_fetch", "review_compare_apps"}
        assert tools["review_fetch"].annotations.readOnlyHint is True
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert prompts == {"find_opportunities", "competitor_teardown"}
        res = await client.call_tool("review_fetch", {"app_id": "appstore:111", "max_reviews": 50, "sample_size": 3})
        assert not res.isError and "FitNow" in res.content[0].text
        bad = await client.call_tool("review_fetch", {"app_id": "111", "country": "turkey"})
        assert bad.isError  # schema validation rejects 3+ letter country


def test_turkish_filler_words_are_not_complaint_terms():
    from review_miner.models import Review
    neg = [Review("appstore", "1", t, rating=1) for t in [
        "Yani giriş yapamıyorum, böyle bir rezalet olmuş, hata veriyor",
        "Yeni güncelleme berbat, giriş yapamıyorum yani, zaman kaybı, hata veriyor",
        "Bana kod gelmiyor, giriş yapamıyorum, kötü, hata veriyor",
    ]]
    terms = {t["term"] for t in top_terms(neg, n=20)}
    assert "giriş yapamıyorum" in terms and "hata veriyor" in terms
    assert not terms & {"yani", "böyle", "olmuş", "yeni", "zaman", "berbat", "kötü", "rezalet", "bana"}


async def test_server_reports_its_own_version():
    from review_miner import __version__
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        info = await client.initialize()
        assert info.serverInfo.version == __version__
