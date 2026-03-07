"""
examples/extract_job_listings.py

Real job listing extraction using AgentAtlas memory.

Two layers:
  1. AgentAtlas registry   - anchors known UI elements (search, filters)
                             0 tokens on warm start, proves the page is known
  2. Deterministic DOM     - extracts job rows by stable HTML structure
                             no LLM needed, no fragile selectors

Why this matters:
  Without AgentAtlas: every agent re-reasons over the full DOM on every run.
  With AgentAtlas:    UI anchors load from memory in milliseconds, 0 tokens.
                      Extraction uses proven deterministic selectors.

Usage:
    pip install agentatlas playwright
    playwright install chromium

    # Direct mode (your own Supabase + OpenAI):
    python3 examples/extract_job_listings.py

    # Hosted API mode:
    AGENTATLAS_API_URL=https://your-api.fly.dev python3 examples/extract_job_listings.py

    # Different board:
    AGENTATLAS_DEMO_URL=https://boards.greenhouse.io/openai python3 examples/extract_job_listings.py
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

DEFAULT_URL = "https://boards.greenhouse.io/anthropic"
DEFAULT_SITE = "boards.greenhouse.io"


async def extract_listings(page, max_jobs: int = 20) -> list[dict]:
    """
    Extract job listings deterministically from a Greenhouse board.
    Current Greenhouse boards expose each job as a direct <a href=".../jobs/..."> node
    with a title line and a metadata line for location.
    """
    jobs = []

    await page.wait_for_selector('a[href*="/jobs/"]', timeout=10000)

    links = await page.query_selector_all('a[href*="/jobs/"]')
    for link_el in links[:max_jobs]:
        try:
            raw_text = (await link_el.inner_text()).strip()
            href = await link_el.get_attribute("href")
            parts = [line.strip() for line in raw_text.splitlines() if line.strip()]
            if not parts:
                continue
            title = parts[0].replace("New", "").strip()
            loc = parts[-1] if len(parts) > 1 else ""
            dept = ""

            if title:
                jobs.append(
                    {
                        "title": title,
                        "department": dept,
                        "location": loc,
                        "url": href if href and href.startswith("http") else f"https://boards.greenhouse.io{href}",
                    }
                )
        except Exception:
            continue

    return jobs


async def main() -> None:
    url = os.getenv("AGENTATLAS_DEMO_URL", DEFAULT_URL)
    site = os.getenv("AGENTATLAS_DEMO_SITE", DEFAULT_SITE)
    tenant = os.getenv("AGENTATLAS_TENANT_ID", "demo-local")
    scope = os.getenv("AGENTATLAS_DEMO_REGISTRY_SCOPE", "private")
    use_api = bool(os.getenv("AGENTATLAS_API_URL"))
    max_jobs = int(os.getenv("AGENTATLAS_DEMO_MAX_JOBS", "20"))

    atlas = Atlas(
        use_api=use_api,
        tenant_id=tenant,
        registry_scope=scope,
    )

    print("AgentAtlas job listing extraction demo")
    print(f"  Site  : {site}")
    print(f"  URL   : {url}")
    print(f"  Mode  : {'hosted API' if use_api else 'direct'}")
    print()

    print("Step 1 - loading UI anchors from registry...")
    t0 = time.perf_counter()
    schema = await atlas.get_schema(site=site, url=url, tenant_id=tenant, registry_scope=scope)
    anchor_ms = int((time.perf_counter() - t0) * 1000)

    print(f"  source      : {schema.source}")
    print(f"  tokens_used : {schema.tokens_used}")
    print(f"  anchors     : {list(schema.elements.keys()) if schema.elements else []}")
    print(f"  elapsed     : {anchor_ms}ms")
    print()

    if schema.source == "registry":
        print("  Warm hit - UI anchors loaded from memory, 0 LLM tokens used")
    elif schema.source == "llm_learned":
        print("  Cold start - page learned and saved to registry")
        print("     Next run will be a warm hit at 0 tokens")
    else:
        print("  Schema not found - proceeding without anchors")
    print()

    print("Step 2 - extracting job listings from page...")
    t1 = time.perf_counter()

    jobs = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()
        if stealth_async:
            await stealth_async(page)

        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)

        if schema.elements:
            anchors_found = 0
            for _name, info in list(schema.elements.items())[:3]:
                selector_type = info.get("type", "")
                selector = info.get("selector", "")
                try:
                    if selector_type == "role" and "+" in selector:
                        role, label = selector.split("+", 1)
                        count = await page.get_by_role(role, name=label).count()
                        if count > 0:
                            anchors_found += 1
                except Exception:
                    pass
            print(f"  Anchor verification: {anchors_found}/{min(3, len(schema.elements))} known elements present")

        jobs = await extract_listings(page, max_jobs=max_jobs)
        await browser.close()

    extract_ms = int((time.perf_counter() - t1) * 1000)
    print(f"  Jobs extracted : {len(jobs)}")
    print(f"  Elapsed        : {extract_ms}ms")
    print()

    if jobs:
        print(f"Job listings ({len(jobs)} found):")
        for i, job in enumerate(jobs[:10], 1):
            print(f"  {i:2}. {job['title']}")
            if job["department"]:
                print(f"      Department : {job['department']}")
            if job["location"]:
                print(f"      Location   : {job['location']}")
            print(f"      URL        : {job['url']}")
        if len(jobs) > 10:
            print(f"  ... and {len(jobs) - 10} more")
    else:
        print("No jobs extracted. The board may be empty or the page structure changed.")
    print()

    print("Summary:")
    print(f"  Registry source  : {schema.source}")
    print(f"  Tokens used      : {schema.tokens_used}")
    print(f"  Anchor load time : {anchor_ms}ms")
    print(f"  Extraction time  : {extract_ms}ms")
    print(f"  Jobs found       : {len(jobs)}")
    print(f"  Warm hit         : {'yes' if schema.source == 'registry' else 'no (first run)'}")
    print()

    output = {
        "site": site,
        "url": url,
        "registry": {"source": schema.source, "tokens_used": schema.tokens_used, "anchor_ms": anchor_ms},
        "extraction": {"job_count": len(jobs), "extract_ms": extract_ms},
        "warm_hit": schema.source == "registry",
        "jobs": jobs,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
