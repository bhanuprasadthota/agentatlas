"""
examples/extract_hn_posts.py

Deterministic Hacker News front page extraction using AgentAtlas memory.

HN is the ideal benchmark for the minimal_static category: pure server-rendered
HTML with virtually no JS, extremely stable selectors, and high post frequency
so the content changes but the structure never does.

Usage:
    python3 examples/extract_hn_posts.py
    AGENTATLAS_DEMO_MAX_ITEMS=30 python3 examples/extract_hn_posts.py
"""

import asyncio
import json
import os
import time

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

from agentatlas import Atlas

DEFAULT_URL = "https://news.ycombinator.com/"
DEFAULT_SITE = "news.ycombinator.com"


async def extract_posts(page, max_items: int = 30) -> list[dict]:
    await page.wait_for_selector(".athing", timeout=10000)
    rows = await page.query_selector_all(".athing")
    posts = []
    for row in rows[:max_items]:
        try:
            title_el = await row.query_selector(".titleline > a")
            rank_el = await row.query_selector(".rank")
            if not title_el:
                continue
            title = (await title_el.inner_text()).strip()
            href = await title_el.get_attribute("href") or ""
            rank = (await rank_el.inner_text()).strip().rstrip(".") if rank_el else ""

            # Score and comment count are in the following sibling row
            subtext = await row.evaluate_handle(
                "el => el.nextElementSibling"
            )
            score_text = ""
            comments_text = ""
            try:
                score_el = await subtext.query_selector(".score")
                comments_el = await subtext.query_selector('a[href*="item?id="]')
                if score_el:
                    score_text = (await score_el.inner_text()).strip()
                if comments_el:
                    comments_text = (await comments_el.inner_text()).strip()
            except Exception:
                pass

            posts.append({
                "rank": rank,
                "title": title,
                "url": href if href.startswith("http") else f"https://news.ycombinator.com/{href}",
                "score": score_text,
                "comments": comments_text,
            })
        except Exception:
            continue
    return posts


async def main() -> None:
    url = os.getenv("AGENTATLAS_DEMO_URL", DEFAULT_URL)
    site = os.getenv("AGENTATLAS_DEMO_SITE", DEFAULT_SITE)
    tenant = os.getenv("AGENTATLAS_TENANT_ID", "demo-local")
    scope = os.getenv("AGENTATLAS_DEMO_REGISTRY_SCOPE", "private")
    use_api = bool(os.getenv("AGENTATLAS_API_URL"))
    max_items = int(os.getenv("AGENTATLAS_DEMO_MAX_ITEMS", "30"))

    atlas = Atlas(use_api=use_api, tenant_id=tenant, registry_scope=scope)

    print("AgentAtlas Hacker News extraction demo")
    print(f"  Site  : {site}")
    print(f"  URL   : {url}")
    print(f"  Mode  : {'hosted API' if use_api else 'direct'}")
    print()

    print("Step 1 - loading page memory...")
    t0 = time.perf_counter()
    schema = await atlas.get_schema(site=site, url=url, tenant_id=tenant, registry_scope=scope)
    anchor_ms = int((time.perf_counter() - t0) * 1000)

    print(f"  source      : {schema.source}")
    print(f"  tokens_used : {schema.tokens_used}")
    print(f"  elapsed     : {anchor_ms}ms")
    if schema.source == "registry":
        print("  Warm hit - 0 LLM tokens used")
    else:
        print("  Cold start - page learned, next run will be 0 tokens")
    print()

    print("Step 2 - extracting posts...")
    t1 = time.perf_counter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        posts = await extract_posts(page, max_items=max_items)
        await browser.close()
    extract_ms = int((time.perf_counter() - t1) * 1000)

    print(f"  Posts extracted : {len(posts)}")
    print(f"  Elapsed         : {extract_ms}ms")
    print()
    for post in posts[:10]:
        print(f"  {post['rank']:>2}. {post['title'][:70]}")
        print(f"      {post['score']}  {post['comments']}  {post['url'][:60]}")
    if len(posts) > 10:
        print(f"  ... and {len(posts) - 10} more")
    print()

    print(json.dumps({
        "site": site,
        "url": url,
        "registry": {"source": schema.source, "tokens_used": schema.tokens_used, "anchor_ms": anchor_ms},
        "extraction": {"post_count": len(posts), "extract_ms": extract_ms},
        "warm_hit": schema.source == "registry",
        "posts": posts,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
