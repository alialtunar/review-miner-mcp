"""Shared HTTP layer: one client, a small TTL cache, one retry, readable errors."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import httpx

USER_AGENT = "review-miner-mcp/0.1 (+https://github.com/alialtunar/review-miner-mcp)"
CACHE_TTL_SECONDS = 15 * 60
_MAX_CONCURRENCY = 4

_cache: dict[str, tuple[float, Any]] = {}
_transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport here
_semaphores: dict[int, asyncio.Semaphore] = {}


class SourceError(Exception):
    """Error with a message that tells the model what to do next."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the network layer (used by tests). Also clears the cache."""
    global _transport
    _transport = transport
    _cache.clear()


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _semaphores:
        _semaphores[loop_id] = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _semaphores[loop_id]


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    return url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


async def get_json(url: str, params: dict[str, Any] | None = None, source: str = "source",
                   cache_if: Callable[[Any], bool] | None = None) -> Any:
    """GET a JSON document with caching and a single retry on 429/5xx.
    cache_if lets callers skip caching responses that are worth retrying later (e.g. empty feeds)."""
    key = _cache_key(url, params)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    async with _semaphore():
        async with httpx.AsyncClient(
            transport=_transport,
            timeout=20.0,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            for attempt in (1, 2):
                try:
                    resp = await client.get(url, params=params)
                except httpx.TimeoutException as e:
                    if attempt == 2:
                        raise SourceError(f"{source} timed out. Try again or lower max_reviews.") from e
                    continue
                except httpx.HTTPError as e:
                    raise SourceError(f"Could not reach {source}: {type(e).__name__}. Check your internet connection.") from e

                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 1:
                    await asyncio.sleep(1.5)
                    continue
                if resp.status_code == 404:
                    raise SourceError(f"{source} returned 404. The app/game ID or country code is probably wrong; use a search tool to find the right ID.")
                if resp.status_code == 429:
                    raise SourceError(f"{source} is rate limiting us. Wait a minute or fetch fewer reviews.")
                if resp.status_code >= 400:
                    raise SourceError(f"{source} returned HTTP {resp.status_code}.")
                try:
                    data = resp.json()
                except ValueError as e:
                    raise SourceError(f"{source} returned something that is not JSON (maybe the ID or country is invalid).") from e
                if cache_if is None or cache_if(data):
                    _cache[key] = (time.monotonic(), data)
                return data
    raise SourceError(f"{source} request failed.")  # pragma: no cover
