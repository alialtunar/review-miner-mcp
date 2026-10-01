"""Live smoke test against the real Apple and Steam endpoints.

Run:  uv run python scripts/smoke_live.py
Every check prints PASS/FAIL so you can see which endpoint changed shape.
"""

import asyncio
import sys

from review_miner.server import review_compare_apps, review_fetch, review_search_apps, review_top_apps

CHECKS = [
    ("App Store top charts (TR, health-fitness)", review_top_apps, dict(category="health-fitness", country="tr", limit=5)),
    ("App Store search (Duolingo)", review_search_apps, dict(query="Duolingo", limit=3)),
    ("App Store reviews (Duolingo, US)", review_fetch, dict(app_id="appstore:570060128", max_reviews=100, sample_size=3)),
    ("Steam top sellers", review_top_apps, dict(store="steam", chart="top_sellers", limit=25)),
    ("Steam most played", review_top_apps, dict(store="steam", chart="most_played", limit=10)),
    ("Steam search (Cyberpunk)", review_search_apps, dict(query="Cyberpunk 2077", store="steam", limit=3)),
    ("Steam reviews (Cyberpunk 2077)", review_fetch, dict(app_id="steam:1091500", max_reviews=100, sample_size=3)),
    ("Compare (App Store + Steam)", review_compare_apps, dict(apps=["appstore:570060128", "steam:1091500"], max_reviews_per_app=50, samples_per_app=1)),
]


async def main() -> int:
    failed = 0
    for name, fn, kwargs in CHECKS:
        out = await fn(**kwargs)
        # An empty Apple feed inside a comparison row must fail too, not just a top-level error.
        ok = not out.startswith("Error") and len(out) > 80 and "feed came back empty" not in out
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        print("      " + out.replace("\n", "\n      ")[:600] + ("\n      …" if len(out) > 600 else ""))
        print()
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
