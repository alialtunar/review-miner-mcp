"""review-miner MCP server.

Tools fetch and pre-digest public reviews; the model does the interpretation.
Transport: stdio (default). No API keys.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import appstore, steam
from .analysis import pick_samples, summarize
from .http import SourceError
from .models import App, Review

mcp = FastMCP(
    "review_miner_mcp",
    instructions=(
        "Mine public App Store and Steam reviews. Typical flow: review_top_apps or "
        "review_search_apps to get IDs -> review_fetch for one app or review_compare_apps "
        "for several -> group complaints into themes and quote short examples. "
        "Stats are computed over all fetched reviews; only a sample of texts is returned."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

StoreName = Literal["appstore", "steam"]
Country = Annotated[str, Field(pattern=r"^[A-Za-z]{2}$", description="2-letter store country, e.g. 'us', 'tr', 'de'.")]
Format = Annotated[Literal["markdown", "json"], Field(description="'markdown' (default) or 'json'.")]


# ---------- rendering ----------

def _app_line(a: App) -> str:
    bits = [f"**{a.name}**" if a.name else "", f"`{a.store}:{a.app_id}`"]
    if a.developer:
        bits.append(a.developer)
    if a.category:
        bits.append(a.category)
    if a.price:
        bits.append(a.price)
    if a.rating is not None:
        bits.append(f"{a.rating}% positive" if a.store == "steam" else f"{a.rating:.2f}★")
    if a.rating_count:
        bits.append(f"{a.rating_count:,} ratings")
    if a.review_summary:
        bits.append(a.review_summary)
    prefix = f"{a.rank}. " if a.rank else "- "
    return prefix + " · ".join(b for b in bits if b)


def _review_line(r: Review) -> str:
    head = f"{r.rating}★" if r.rating is not None else ("👍" if r.recommended else "👎")
    meta = [m for m in (r.date, f"v{r.version}" if r.version else None,
                        f"{r.playtime_hours}h played" if r.playtime_hours else None,
                        f"{r.helpful_votes} helpful" if r.helpful_votes else None) if m]
    d = r.to_dict()
    title = f"**{r.title}** — " if r.title else ""
    return f"- {head} {title}{d['text'] if 'text' in d else ''} _({', '.join(meta)})_"


def _stats_md(stats: dict[str, Any]) -> list[str]:
    lines = []
    for k, v in stats.items():
        if k == "top_complaint_terms":
            if v:
                terms = ", ".join(
                    f"{t['term']} ({t['share']}" + (f", {t['x_vs_positive']}× vs positive)" if "x_vs_positive" in t else ")")
                    for t in v)
                lines.append(f"- **Top complaint terms** (share of negative reviews): {terms}")
        elif k == "rating_by_version":
            if v:
                lines.append("- **Rating by version (newest first):** " +
                             ", ".join(f"v{x['version']}: {x['avg']}★ (n={x['reviews']})" for x in v))
        elif k == "star_distribution":
            lines.append("- **Stars:** " + " ".join(f"{s}={c}" for s, c in v.items()))
        else:
            lines.append(f"- **{k.replace('_', ' ').capitalize()}:** {v}")
    return lines


def _error(e: Exception) -> str:
    if isinstance(e, SourceError):
        return f"Error: {e}"
    return f"Error: unexpected {type(e).__name__}: {e}"


# ---------- core fetch used by two tools ----------

async def _fetch(store: StoreName, app_id: str, country: str, only: str, max_reviews: int,
                 language: str) -> tuple[App | None, list[Review], list[Review]]:
    """Returns (app, all_fetched, filtered)."""
    if store == "appstore":
        app, reviews = await asyncio.gather(
            appstore.lookup_app(app_id, country),
            appstore.fetch_reviews(app_id, country, max_reviews),
        )
    else:
        (reviews, app), name = await asyncio.gather(
            steam.fetch_reviews(app_id, max_reviews, only, language),  # type: ignore[arg-type]
            steam.app_name(app_id),
        )
        if app:
            app.name = name
    if only == "negative":
        filtered = [r for r in reviews if r.is_negative]
    elif only == "positive":
        filtered = [r for r in reviews if not r.is_negative]
    else:
        filtered = reviews
    return app, reviews, filtered


# ---------- tools ----------

@mcp.tool(name="review_top_apps", annotations=READ_ONLY)
async def review_top_apps(
    store: Annotated[StoreName, Field(description="'appstore' or 'steam'.")] = "appstore",
    category: Annotated[str | None, Field(description="App Store only, e.g. 'health-fitness', 'finance', 'games', 'productivity'. Omit for all.")] = None,
    country: Country = "us",
    chart: Annotated[str, Field(description="App Store: 'free' | 'paid' | 'grossing'. Steam: 'top_sellers' | 'new_releases'.")] = "free",
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    response_format: Format = "markdown",
) -> str:
    """List the current top charts (App Store by category, or Steam top sellers / new releases).
    Use this to pick competitors before fetching their reviews."""
    try:
        if store == "appstore":
            apps = await appstore.top_apps(category, country.lower(), chart, limit)
        else:
            steam_chart = chart if chart in ("top_sellers", "new_releases") else "top_sellers"
            apps = await steam.top_games(country, steam_chart, limit)
    except Exception as e:
        return _error(e)
    if not apps:
        return "No apps returned. Try another category/country."
    if response_format == "json":
        return json.dumps([a.to_dict() for a in apps], ensure_ascii=False, indent=2)
    title = f"Top {chart} · {store} · {country.upper()}" + (f" · {category}" if category else "")
    return "\n".join([f"## {title}", *(_app_line(a) for a in apps)])


@mcp.tool(name="review_search_apps", annotations=READ_ONLY)
async def review_search_apps(
    query: Annotated[str, Field(min_length=1, max_length=100, description="App or game name, e.g. 'Duolingo' or 'Cyberpunk'.")],
    store: Annotated[StoreName, Field(description="'appstore' or 'steam'.")] = "appstore",
    country: Country = "us",
    limit: Annotated[int, Field(ge=1, le=25)] = 8,
    response_format: Format = "markdown",
) -> str:
    """Find an app/game and its ID by name. Returns IDs in 'store:id' form for the other tools."""
    try:
        if store == "appstore":
            apps = await appstore.search_apps(query, country.lower(), limit)
        else:
            apps = await steam.search_games(query, country, limit)
    except Exception as e:
        return _error(e)
    if not apps:
        return f"No results for '{query}' on {store} ({country.upper()})."
    if response_format == "json":
        return json.dumps([a.to_dict() for a in apps], ensure_ascii=False, indent=2)
    return "\n".join([f"## Search '{query}' · {store} · {country.upper()}", *(_app_line(a) for a in apps)])


@mcp.tool(name="review_fetch", annotations=READ_ONLY)
async def review_fetch(
    app_id: Annotated[str, Field(description="Numeric ID from a search/top tool, e.g. '570060911' or 'appstore:570060911'.")],
    store: Annotated[StoreName, Field(description="'appstore' or 'steam'. Ignored if app_id has a 'store:' prefix.")] = "appstore",
    country: Country = "us",
    only: Annotated[Literal["all", "negative", "positive"], Field(description="Which review texts to return. 'negative' = 1-2★ / thumbs down.")] = "negative",
    max_reviews: Annotated[int, Field(ge=10, le=500, description="How many recent reviews to analyze. App Store caps near 500.")] = 200,
    sample_size: Annotated[int, Field(ge=0, le=100, description="How many review texts to include in the output.")] = 30,
    language: Annotated[str, Field(description="Steam only: 'english', 'turkish', 'all', ...")] = "english",
    response_format: Format = "markdown",
) -> str:
    """Fetch recent reviews for one app/game and return stats (rating mix, rating per version,
    top complaint terms) plus the most helpful review texts. Best for 'why do users hate X?'."""
    store, app_id = _split_id(app_id, store)
    try:
        app, all_reviews, filtered = await _fetch(store, app_id, country.lower(), only, max_reviews, language)
        baseline = None
        if store == "steam" and only == "negative":  # contrast complaints with praise
            baseline, _ = await steam.fetch_reviews(app_id, 100, "positive", language)
    except Exception as e:
        return _error(e)
    stats = summarize(all_reviews, baseline, app.name if app else "")
    if store == "steam" and only != "all":
        stats.pop("negative_share", None)  # Steam already filtered server-side; overall score is in app line
    samples = pick_samples(filtered, sample_size)

    if response_format == "json":
        return json.dumps({
            "app": app.to_dict() if app else {"store": store, "app_id": app_id},
            "stats": stats,
            "samples": [r.to_dict() for r in samples],
        }, ensure_ascii=False, indent=2)

    lines = [f"## Reviews · `{store}:{app_id}` · {country.upper()}"]
    if app:
        lines.append(_app_line(app))
    lines += ["", "### Stats", *_stats_md(stats), "", f"### {len(samples)} {only} review samples (most helpful first)"]
    lines += [_review_line(r) for r in samples] or ["_No matching reviews in this window._"]
    return "\n".join(lines)


@mcp.tool(name="review_compare_apps", annotations=READ_ONLY)
async def review_compare_apps(
    apps: Annotated[list[str], Field(min_length=2, max_length=8, description="IDs as 'appstore:123' or 'steam:456'. Mix allowed.")],
    country: Country = "us",
    max_reviews_per_app: Annotated[int, Field(ge=20, le=300)] = 150,
    samples_per_app: Annotated[int, Field(ge=0, le=10)] = 5,
    language: Annotated[str, Field(description="Steam only.")] = "english",
    response_format: Format = "markdown",
) -> str:
    """Compare recent reviews of 2-8 competitors side by side: rating, negative share and
    top complaint terms per app, plus a few negative samples each. Use it to find gaps
    that every competitor fails at (= product opportunity)."""
    parsed = [_split_id(a, "appstore") for a in apps]

    async def one(store: StoreName, app_id: str) -> dict[str, Any]:
        try:
            # Steam: fetch 'all' so negative share is comparable with App Store.
            app, all_reviews, _ = await _fetch(store, app_id, country.lower(), "all", max_reviews_per_app, language)
        except Exception as e:
            return {"id": f"{store}:{app_id}", "error": _error(e)}
        stats = summarize(all_reviews, app_name=app.name if app else "")
        negs = pick_samples([r for r in all_reviews if r.is_negative], samples_per_app)
        return {"id": f"{store}:{app_id}", "app": app, "stats": stats, "samples": negs}

    results = await asyncio.gather(*(one(s, i) for s, i in parsed))

    if response_format == "json":
        return json.dumps([
            {**({"error": r["error"]} if "error" in r else {
                "app": r["app"].to_dict() if r["app"] else None,
                "stats": r["stats"], "samples": [s.to_dict() for s in r["samples"]]}),
             "id": r["id"]} for r in results
        ], ensure_ascii=False, indent=2)

    table = ["| App | Overall | Recent reviews | Negative | Top complaint terms |", "|---|---|---|---|---|"]
    details: list[str] = []
    for r in results:
        if "error" in r:
            table.append(f"| `{r['id']}` | — | — | — | {r['error']} |")
            continue
        a, st = r["app"], r["stats"]
        name = (a.name if a and a.name else "") or r["id"]
        overall = "—"
        if a and a.rating is not None:
            overall = f"{a.rating}% 👍" if a.store == "steam" else f"{a.rating:.2f}★"
        terms = ", ".join(t["term"] for t in st.get("top_complaint_terms", [])[:5]) or "—"
        n = st.get("reviews_analyzed", 0)
        if n == 0:
            terms = f"no recent reviews in the {country.upper()} store"
        table.append(f"| {name} | {overall} | {n} | {st.get('negative_share', '—')} | {terms} |")
        if r["samples"]:
            details += ["", f"### {name}", *(_review_line(s) for s in r["samples"])]
    return "\n".join([f"## Competitor review comparison · {country.upper()}", *table, *details])


def _split_id(raw: str, default_store: StoreName) -> tuple[StoreName, str]:
    raw = raw.strip()
    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        if prefix in ("appstore", "steam"):
            return prefix, rest.strip()  # type: ignore[return-value]
    return default_store, raw


# ---------- prompts ----------

@mcp.prompt(name="find_opportunities", description="Find product gaps in a category by mining competitors' negative reviews.")
def find_opportunities(category: str = "health-fitness", country: str = "us", store: str = "appstore") -> str:
    return (
        f"1. Call review_top_apps(store='{store}', category='{category}', country='{country}', limit=15).\n"
        f"2. Pick the 5 most relevant competitors and call review_compare_apps on them (country='{country}').\n"
        "3. Group the complaints into 5-7 themes. For each theme give: how many of the 5 apps suffer from it, "
        "a short quote (under 15 words) as evidence, and whether it looks fixable by a new product.\n"
        "4. Finish with the top 3 opportunities as a table: Problem | Evidence | Who suffers | Product idea.\n"
        "Be concrete; do not invent numbers that are not in the tool output."
    )


@mcp.prompt(name="competitor_teardown", description="Deep dive into what users love and hate about one app or game.")
def competitor_teardown(app_name: str, store: str = "appstore", country: str = "us") -> str:
    return (
        f"1. review_search_apps(query='{app_name}', store='{store}', country='{country}') and pick the right ID.\n"
        "2. review_fetch with only='negative', max_reviews=300, then again with only='positive', sample_size=15.\n"
        "3. Report: what users love (keep), what they hate (themes with counts), whether a recent version made "
        "ratings drop (use rating_by_version), and the 3 things a competitor should do better."
    )


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep stderr quiet
    logging.getLogger("mcp").setLevel(logging.WARNING)    # no "Processing request" line per call
    mcp.run()


if __name__ == "__main__":
    main()
