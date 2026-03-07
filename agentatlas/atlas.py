"""
atlas.py — The complete AgentAtlas SDK
"""

import re
import json
import os
import base64
import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.async_api import async_playwright
try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None
from openai import OpenAI
from agentatlas.supabase_client import get_supabase

load_dotenv()

@dataclass
class SiteSchema:
    site: str
    url: str
    route_key: str
    status: str
    confidence: float
    elements: dict
    source: str
    tokens_used: int
    message: str

class Atlas:
    def __init__(self):
        self.sb     = get_supabase()
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def get_schema(self, site: str, url: str) -> SiteSchema:
        print(f"\n[AgentAtlas] Looking up: {site}")
        schema = await self._fetch_from_registry(site, url)
        if schema:
            print(f"[AgentAtlas] ✅ Registry hit — 0 tokens used")
            return SiteSchema(
                site=site, url=url,
                route_key=schema["route_key"],
                status="found", confidence=schema["confidence"],
                elements=schema["elements"], source="registry",
                tokens_used=0, message="Schema found in registry. No LLM used."
            )
        print(f"[AgentAtlas] ⚠ Not in registry. Crawling and learning...")
        learned = await self._learn_site(site, url)
        if not learned:
            return SiteSchema(
                site=site, url=url, route_key="unknown",
                status="not_found", confidence=0.0, elements={},
                source="not_found", tokens_used=0,
                message="Could not learn site. Page may be blocked or empty."
            )
        await self._save_to_registry(site, url, learned)
        print(f"[AgentAtlas] 💾 Saved to registry — next user gets this free")
        return SiteSchema(
            site=site, url=url, route_key=learned["route_key"],
            status="learned", confidence=0.6, elements=learned["elements"],
            source="llm_learned", tokens_used=learned["tokens_used"],
            message=f"Schema learned and saved. Tokens used: {learned['tokens_used']}."
        )

    async def _fetch_from_registry(self, site: str, url: str) -> dict | None:
        site_row = self.sb.table("sites").select("id").eq("domain", site).limit(1).execute().data
        if not site_row:
            return None
        site_id = site_row[0]["id"]
        routes = self.sb.table("page_routes").select("id, route_key, path_pattern").eq("site_id", site_id).execute().data
        matched = self._match_route(url, routes)
        if not matched:
            return None
        playbooks = self.sb.table("playbooks").select("payload, confidence").eq("site_id", site_id).eq("route_id", matched["id"]).eq("status", "active").order("confidence", desc=True).limit(1).execute().data
        if not playbooks:
            return None
        elements = self._build_elements(playbooks[0]["payload"])
        if not elements:
            return None
        return {"route_key": matched["route_key"], "confidence": playbooks[0]["confidence"], "elements": elements}

    async def _crawl_page(self, url: str) -> dict:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                    locale="en-US", timezone_id="America/New_York",
                )
                page = await context.new_page()
                if stealth_async:
                    await stealth_async(page)
                    print(f"[AgentAtlas] 🥷 Stealth mode active")
                await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}", lambda r: r.abort())
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception:
                    pass
                for wait_ms in [2000, 2000, 2000]:
                    await page.wait_for_timeout(wait_ms)
                    count = await page.evaluate("() => document.querySelectorAll('a, button').length")
                    print(f"[AgentAtlas] ⏳ Interactive elements: {count}")
                    if count > 10:
                        break

                # CAPTURE 1: Accessibility Tree (Claude-in-Chrome approach)
                print(f"[AgentAtlas] 📋 Capturing accessibility tree...")
                snapshot = await page.accessibility.snapshot(interesting_only=True)
                acc_nodes = []
                def flatten(node, depth=0):
                    if not node:
                        return
                    acc_nodes.append({
                        "role": node.get("role", ""),
                        "name": node.get("name", "")[:100],
                        "value": node.get("value", ""),
                        "level": depth,
                    })
                    for child in node.get("children", []):
                        flatten(child, depth + 1)
                flatten(snapshot)
                print(f"[AgentAtlas] 📋 {len(acc_nodes)} accessibility nodes captured")

                # CAPTURE 2: Screenshot for GPT-4o Vision
                print(f"[AgentAtlas] 📸 Taking screenshot...")
                await page.unroute("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}")
                await page.wait_for_timeout(1000)
                screenshot_bytes = await page.screenshot(full_page=False, type="jpeg", quality=60)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

                await browser.close()
                return {"acc_nodes": acc_nodes, "screenshot_base64": screenshot_base64}
        except Exception as e:
            print(f"[AgentAtlas] Crawl error: {e}")
            return {}

    async def _learn_site(self, site: str, url: str) -> dict | None:
        crawled = await self._crawl_page(url)
        acc_nodes = crawled.get("acc_nodes", [])
        screenshot_base64 = crawled.get("screenshot_base64", "")
        if len(acc_nodes) < 5:
            print(f"[AgentAtlas] ❌ Too few nodes ({len(acc_nodes)}) — page may be blocked")
            return None
        print(f"[AgentAtlas] 🔍 {len(acc_nodes)} nodes + screenshot → GPT-4o Vision...")
        prompt_text = f"""You are building a browser automation schema for {site}.
You can see: 1) A screenshot of {url}  2) The accessibility tree below.

Using BOTH visual layout AND accessibility tree, identify KEY interactive elements.

Return ONLY valid JSON:
{{
  "route_key": "job_list | job_detail | search | product | home | article | other",
  "elements": {{
    "descriptive_purpose_name": {{
      "type": "role | aria_label | text | data_testid | css",
      "selector": "stable selector value",
      "confidence": 0.0
    }}
  }}
}}

Rules:
- Use accessibility tree role+name as selectors (most stable)
- Confidence >= 0.5 only, 3-8 elements max
- Prefer: aria-label, data-testid, role+name
- Avoid: hashed CSS classes

Accessibility tree:
{json.dumps(acc_nodes[:100], indent=1)}"""

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_base64}",
                            "detail": "low"
                        }
                    },
                    {"type": "text", "text": prompt_text}
                ]
            }],
            temperature=0,
            response_format={"type": "json_object"},
        )
        tokens_used = response.usage.total_tokens
        labeled = json.loads(response.choices[0].message.content.strip())
        print(f"[AgentAtlas] 🤖 GPT-4o Vision labeled {len(labeled.get('elements', {}))} elements ({tokens_used} tokens)")
        return {
            "route_key": labeled.get("route_key", "unknown"),
            "elements": labeled.get("elements", {}),
            "tokens_used": tokens_used,
            "raw_payload": labeled,
        }

    async def _save_to_registry(self, site: str, url: str, learned: dict):
        try:
            self.sb.table("sites").upsert({"domain": site, "display_name": site}, on_conflict="domain").execute()
            site_id = self.sb.table("sites").select("id").eq("domain", site).limit(1).execute().data[0]["id"]
            path = urlparse(url).path or "/"
            route_key = learned.get("route_key", "unknown")
            self.sb.table("page_routes").upsert(
                {"site_id": site_id, "route_key": route_key, "path_pattern": f"^{re.escape(path)}", "example_url": url},
                on_conflict="site_id,route_key"
            ).execute()
            route_id = self.sb.table("page_routes").select("id").eq("site_id", site_id).eq("route_key", route_key).limit(1).execute().data[0]["id"]
            self.sb.table("tasks").upsert({"task_key": "generic_extract", "description": "Generic extraction task"}, on_conflict="task_key").execute()
            task_id = self.sb.table("tasks").select("id").eq("task_key", "generic_extract").limit(1).execute().data[0]["id"]
            locators = {}
            for purpose, info in learned.get("elements", {}).items():
                if info.get("confidence", 0) >= 0.5:
                    locators[purpose] = [{"type": info.get("type"), "value": info.get("selector"), "priority": 1, "confidence": info.get("confidence")}]
            self.sb.table("playbooks").upsert(
                {
                    "site_id": site_id, "route_id": route_id, "task_id": task_id,
                    "variant_key": "desktop_enUS_loggedout", "version": 1,
                    "status": "active", "confidence": 0.6, "ttl_days": 14,
                    "payload": {"locators": locators, "fingerprint_source": "llm_vision_learned", "source_url": url},
                },
                on_conflict="site_id,route_id,task_id,variant_key,version"
            ).execute()
        except Exception as e:
            print(f"[AgentAtlas] ⚠ Save failed: {e}")

    def _match_route(self, url: str, routes: list) -> dict | None:
        try:
            path = urlparse(url).path
        except Exception:
            path = url
        for route in routes:
            try:
                if re.search(route["path_pattern"], path):
                    return route
            except Exception:
                continue
        return routes[0] if routes else None

    def _build_elements(self, payload: dict) -> dict:
        locators = payload.get("locators", {})
        elements = {}
        for purpose, locs in locators.items():
            if not locs:
                continue
            best = sorted(locs, key=lambda x: x.get("priority", 99))[0]
            elements[purpose] = {"type": best.get("type"), "selector": best.get("value"), "confidence": best.get("confidence", 0.5)}
        return elements
