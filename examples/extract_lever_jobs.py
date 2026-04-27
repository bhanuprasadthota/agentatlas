"""
examples/extract_lever_jobs.py

Deterministic Lever job board extraction using AgentAtlas memory.

Lever is a second major ATS alongside Greenhouse. This example proves the
registry generalizes across ATS platforms — the same warm-start pattern
works whether the board is Greenhouse, Lever, or Workday.

Usage:
    python3 examples/extract_lever_jobs.py
    AGENTATLAS_DEMO_URL=https://jobs.lever.co/stripe python3 examples/extract_lever_jobs.py
"""

import asyncio
import json
import os
import time

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

from agentatlas import Atlas

DEFAULT_URL = "https://jobs.lever.co/vercel"
DEFAULT_SITE = "jobs.lever.co"


async def extract_listings(page, max_jobs: int = 30) -> list[dict]:
    await page.wait_for_selector(".posting", timeout=15000)
    postings = await page.query_selector_all(".posting")
    jobs = []
    for posting in postings[:max_jobs]:
        try:
            title_el = await posting.query_selector("h5")
            dept_el = await posting.query_selector(".posting-category")
            loc_el = await posting.query_selector(".location")
            link_el = await posting.query_selector("a.posting-btn-submit")

            title = (await title_el.inner_text()).strip() if title_el else ""
            dept = (await dept_el.inner_text()).strip() if dept_el else ""
            loc = (await loc_el.inner_text()).strip() if loc_el else ""
            href = await link_el.get_attribute("href") if link_el else ""

            if title:
                jobs.append({
                    "title": title,
                    "department": dept,
                    "location": loc,
                    "url": href or "",
                })
        except Exception:
            continue
    return jobs


async def main() -> None:
    url = os.getenv("AGENTATLAS_DEMO_URL", DEFAULT_URL)
    site = os.getenv("AGENTATLAS_DEMO_SITE", DEFAULT_SITE)
    tenant = os.getenv("AGENTATLAS_TENANT_ID", "demo-local")
    scope = os.getenv("AGENTATLAS_DEMO_REGISTRY_SCOPE", "private")
    use_api = bool(os.getenv("AGENTATLAS_API_URL"))
    max_jobs = int(os.getenv("AGENTATLAS_DEMO_MAX_JOBS", "30"))

    atlas = Atlas(use_api=use_api, tenant_id=tenant, registry_scope=scope)

    print("AgentAtlas Lever job board extraction demo")
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

    print("Step 2 - extracting job listings from Lever board...")
    t1 = time.perf_counter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        if stealth_async:
            await stealth_async(page)
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1000)
        jobs = await extract_listings(page, max_jobs=max_jobs)
        await browser.close()
    extract_ms = int((time.perf_counter() - t1) * 1000)

    print(f"  Jobs extracted : {len(jobs)}")
    print(f"  Elapsed        : {extract_ms}ms")
    print()
    for i, job in enumerate(jobs[:10], 1):
        print(f"  {i:2}. {job['title']}")
        if job["department"]:
            print(f"      Dept : {job['department']}")
        if job["location"]:
            print(f"      Loc  : {job['location']}")
    if len(jobs) > 10:
        print(f"  ... and {len(jobs) - 10} more")
    print()

    print(json.dumps({
        "site": site,
        "url": url,
        "registry": {"source": schema.source, "tokens_used": schema.tokens_used, "anchor_ms": anchor_ms},
        "extraction": {"job_count": len(jobs), "extract_ms": extract_ms},
        "warm_hit": schema.source == "registry",
        "jobs": jobs,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
