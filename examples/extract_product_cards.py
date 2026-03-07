"""
examples/extract_product_cards.py

Deterministic product-card extraction using AgentAtlas memory.

Targets books.toscrape.com to demonstrate a second supported vertical:
catalog/product listing pages.
"""

import asyncio
import json
import os
import time

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from agentatlas import Atlas

load_dotenv()

DEFAULT_URL = "https://books.toscrape.com/"
DEFAULT_SITE = "books.toscrape.com"


async def extract_products(page, max_items: int = 20) -> list[dict]:
    await page.wait_for_selector("article.product_pod", timeout=10000)
    cards = await page.query_selector_all("article.product_pod")
    products = []
    for card in cards[:max_items]:
        try:
            link = await card.query_selector("h3 a")
            price = await card.query_selector(".price_color")
            availability = await card.query_selector(".availability")
            title = (await link.get_attribute("title")) or ""
            href = await link.get_attribute("href") if link else ""
            price_text = (await price.inner_text()).strip() if price else ""
            availability_text = (await availability.inner_text()).strip() if availability else ""
            if title:
                products.append(
                    {
                        "title": title.strip(),
                        "price": price_text,
                        "availability": " ".join(availability_text.split()),
                        "url": f"https://books.toscrape.com/{href.lstrip('./')}" if href else "",
                    }
                )
        except Exception:
            continue
    return products


async def main() -> None:
    url = os.getenv("AGENTATLAS_DEMO_URL", DEFAULT_URL)
    site = os.getenv("AGENTATLAS_DEMO_SITE", DEFAULT_SITE)
    tenant = os.getenv("AGENTATLAS_TENANT_ID", "demo-local")
    scope = os.getenv("AGENTATLAS_DEMO_REGISTRY_SCOPE", "private")
    use_api = bool(os.getenv("AGENTATLAS_API_URL"))
    max_items = int(os.getenv("AGENTATLAS_DEMO_MAX_ITEMS", "20"))

    atlas = Atlas(use_api=use_api, tenant_id=tenant, registry_scope=scope)

    print("AgentAtlas product card extraction demo")
    print(f"  Site  : {site}")
    print(f"  URL   : {url}")
    print(f"  Mode  : {'hosted API' if use_api else 'direct'}")
    print()

    t0 = time.perf_counter()
    schema = await atlas.get_schema(site=site, url=url, tenant_id=tenant, registry_scope=scope)
    anchor_ms = int((time.perf_counter() - t0) * 1000)

    print("Step 1 - loading page memory...")
    print(f"  source      : {schema.source}")
    print(f"  tokens_used : {schema.tokens_used}")
    print(f"  anchors     : {list(schema.elements.keys()) if schema.elements else []}")
    print(f"  elapsed     : {anchor_ms}ms")
    print()

    t1 = time.perf_counter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        products = await extract_products(page, max_items=max_items)
        await browser.close()
    extract_ms = int((time.perf_counter() - t1) * 1000)

    print(f"Products extracted: {len(products)}")
    for idx, product in enumerate(products[:10], 1):
        print(f"  {idx:2}. {product['title']} — {product['price']} — {product['availability']}")
    if len(products) > 10:
        print(f"  ... and {len(products) - 10} more")
    print()
    print(
        json.dumps(
            {
                "site": site,
                "url": url,
                "registry": {"source": schema.source, "tokens_used": schema.tokens_used, "anchor_ms": anchor_ms},
                "extraction": {"product_count": len(products), "extract_ms": extract_ms},
                "warm_hit": schema.source == "registry",
                "products": products,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
