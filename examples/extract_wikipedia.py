"""
examples/extract_wikipedia.py

Deterministic Wikipedia article extraction using AgentAtlas memory.

Demonstrates the content_page vertical: AgentAtlas learns the heading
and section structure once; subsequent runs load from memory at 0 tokens
and extract structured article content deterministically.

Usage:
    python3 examples/extract_wikipedia.py
    AGENTATLAS_DEMO_URL=https://en.wikipedia.org/wiki/Rust_(programming_language) \\
        python3 examples/extract_wikipedia.py
"""

import asyncio
import json
import os
import time

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

from agentatlas import Atlas

DEFAULT_URL = "https://en.wikipedia.org/wiki/Python_(programming_language)"
DEFAULT_SITE = "en.wikipedia.org"


async def extract_article(page, max_sections: int = 10) -> dict:
    await page.wait_for_selector("#firstHeading", timeout=10000)

    title = await page.inner_text("#firstHeading")
    summary_el = await page.query_selector("#mw-content-text .mw-parser-output > p:not(.mw-empty-elt)")
    summary = (await summary_el.inner_text()).strip() if summary_el else ""

    sections = []
    headings = await page.query_selector_all("#mw-content-text h2, #mw-content-text h3")
    for heading in headings[:max_sections]:
        try:
            text = (await heading.inner_text()).strip()
            if text and text not in ("Contents", "References", "External links", "See also", "Notes"):
                sections.append(text)
        except Exception:
            continue

    infobox = {}
    rows = await page.query_selector_all(".infobox tr")
    for row in rows[:15]:
        try:
            label_el = await row.query_selector("th")
            value_el = await row.query_selector("td")
            if label_el and value_el:
                label = (await label_el.inner_text()).strip()
                value = " ".join((await value_el.inner_text()).split())
                if label and value:
                    infobox[label] = value
        except Exception:
            continue

    return {
        "title": title.strip(),
        "summary": summary[:400] + "..." if len(summary) > 400 else summary,
        "sections": sections,
        "infobox": infobox,
    }


async def main() -> None:
    url = os.getenv("AGENTATLAS_DEMO_URL", DEFAULT_URL)
    site = os.getenv("AGENTATLAS_DEMO_SITE", DEFAULT_SITE)
    tenant = os.getenv("AGENTATLAS_TENANT_ID", "demo-local")
    scope = os.getenv("AGENTATLAS_DEMO_REGISTRY_SCOPE", "private")
    use_api = bool(os.getenv("AGENTATLAS_API_URL"))

    atlas = Atlas(use_api=use_api, tenant_id=tenant, registry_scope=scope)

    print("AgentAtlas Wikipedia extraction demo")
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
    print(f"  anchors     : {list(schema.elements.keys()) if schema.elements else []}")
    print(f"  elapsed     : {anchor_ms}ms")
    if schema.source == "registry":
        print("  Warm hit - 0 LLM tokens used")
    else:
        print("  Cold start - page learned, next run will be 0 tokens")
    print()

    print("Step 2 - extracting article content...")
    t1 = time.perf_counter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        article = await extract_article(page)
        await browser.close()
    extract_ms = int((time.perf_counter() - t1) * 1000)

    print(f"  Title    : {article['title']}")
    print(f"  Sections : {len(article['sections'])}")
    print(f"  Infobox  : {len(article['infobox'])} fields")
    print(f"  Elapsed  : {extract_ms}ms")
    print()

    print(json.dumps({
        "site": site,
        "url": url,
        "registry": {"source": schema.source, "tokens_used": schema.tokens_used, "anchor_ms": anchor_ms},
        "extraction": {"extract_ms": extract_ms},
        "warm_hit": schema.source == "registry",
        "article": article,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
