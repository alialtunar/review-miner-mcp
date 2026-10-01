"""Terminal demo: top apps in a category -> side-by-side complaint comparison.

Run:  uv run --with rich python scripts/demo.py health-fitness tr   (rich is optional, for pretty tables)
Calls the same tool functions the MCP server exposes, so the output is what Claude sees.
"""

import asyncio
import json
import logging
import sys

from review_miner.server import review_compare_apps, review_top_apps


async def main(category: str, country: str) -> None:
    print(f"→ review_top_apps(category='{category}', country='{country}', limit=5)")
    top = json.loads(await review_top_apps(category=category, country=country, limit=5, response_format="json"))
    apps = top["apps"] if isinstance(top, dict) else top
    for a in apps:
        print(f"  #{a['rank']} {a['name']}")
    ids = [f"appstore:{a['app_id']}" for a in apps]
    print(f"\n→ review_compare_apps({len(ids)} apps, country='{country}')\n")
    out = await review_compare_apps(apps=ids, country=country, max_reviews_per_app=150, samples_per_app=0)
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        Console(width=120).print(Markdown(out))
    except ImportError:
        print(out)


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = sys.argv[1:]
    asyncio.run(main(args[0] if args else "health-fitness", args[1] if len(args) > 1 else "tr"))
