"""
atlas.py — The complete AgentAtlas SDK

Flow:
  1. Developer calls get_schema(site, url)
  2. Check DB → found? return immediately (0 tokens)
  3. Not found? → accessibility tree + screenshot → GPT-4o Vision → save → return
  4. execute() → multi-step agent loop using registry at every page transition
"""

import re
import json
import os
import base64
import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page
try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None
from openai import OpenAI
from agentatlas.supabase_client import get_supabase

load_dotenv()


# ─────────────────────────────────────────────────────────────
# Return types
# ─────────────────────────────────────────────────────────────
@dataclass
class SiteSchema:
    site: str
    url: str
    route_key: str
    status: str           # "found" | "learned" | "not_found"
    confidence: float
    elements: dict
    source: str           # "registry" | "llm_learned" | "not_found"
    tokens_used: int
    message: str


@dataclass
class ExecuteResult:
    site: str
    task: str
    status: str           # "done" | "partial" | "failed"
    steps_taken: int
    total_tokens: int
    data: dict            # extracted data
    history: list         # step by step log


# ─────────────────────────────────────────────────────────────
# Main SDK class
# ─────────────────────────────────────────────────────────────
class Atlas:
    def __init__(self):
        self.sb     = get_supabase()
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._session_cache = {}  # url → elements, avoids re-learning same page in one session

    # ─────────────────────────────────────────────
    # PUBLIC: get schema for a page
    # ─────────────────────────────────────────────
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

    # ─────────────────────────────────────────────
    # PUBLIC: execute a multi-step task
    # Browser stays open across all page transitions
    # get_schema() called on every new page — 0 tokens if registry hit
    # ─────────────────────────────────────────────
    async def execute(
        self,
        site: str,
        url: str,
        task: str,
        variant: str = "loggedout",
        max_steps: int = 10,
    ) -> ExecuteResult:
        print(f"\n[AgentAtlas] 🚀 Executing: {task}")
        print(f"[AgentAtlas] 🌐 Starting at: {url}")

        total_tokens = 0
        history      = []
        extracted    = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()
            if stealth_async:
                await stealth_async(page)

            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            for step in range(max_steps):
                current_url = page.url
                print(f"\n[AgentAtlas] 📍 Step {step + 1} — {current_url}")

                # Get schema for current page (0 tokens if registry or session cache hit)
                domain = urlparse(current_url).netloc.replace("www.", "")
                cache_key = f"{domain}:{urlparse(current_url).path}"

                if cache_key in self._session_cache:
                    print(f"[AgentAtlas] ⚡ Session cache hit — 0 tokens")
                    elements = self._session_cache[cache_key]
                else:
                    schema = await self._fetch_from_registry(domain, current_url)
                    if schema:
                        print(f"[AgentAtlas] ✅ Registry hit — 0 tokens")
                        elements = schema["elements"]
                        self._session_cache[cache_key] = elements
                    else:
                        print(f"[AgentAtlas] 🔍 Learning current page...")
                        learned = await self._learn_page_from_browser(page, domain, current_url)
                        if learned:
                            await self._save_to_registry(domain, current_url, learned)
                            elements = learned["elements"]
                            total_tokens += learned["tokens_used"]
                            self._session_cache[cache_key] = elements
                        else:
                            elements = {}

                # Screenshot for LLM context
                screenshot_bytes  = await page.screenshot(full_page=False, type="jpeg", quality=50)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

                # Ask LLM what to do next
                action, action_tokens = await self._decide_action(
                    task=task,
                    current_url=current_url,
                    elements=elements,
                    screenshot_base64=screenshot_base64,
                    history=history,
                    extracted=extracted,
                )
                total_tokens += action_tokens
                print(f"[AgentAtlas] 🤖 Action: {action}")
                history.append({"step": step + 1, "url": current_url, "action": action})

                # Execute the action
                if action["type"] == "done":
                    print(f"[AgentAtlas] ✅ Task complete!")
                    if action.get("data"):
                        extracted.update(action["data"])
                    await browser.close()
                    return ExecuteResult(
                        site=site, task=task, status="done",
                        steps_taken=step + 1, total_tokens=total_tokens,
                        data=extracted, history=history,
                    )

                elif action["type"] == "click":
                    success = await self._do_click(page, action, elements)
                    if success:
                        await page.wait_for_timeout(2000)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass

                elif action["type"] == "type":
                    success = await self._do_type(page, action, elements)
                    if success:
                        await page.wait_for_timeout(1000)

                elif action["type"] == "extract":
                    data = await self._do_extract(page, action, elements)
                    if data:
                        extracted.update(data)
                        print(f"[AgentAtlas] 📦 Extracted: {list(data.keys())}")

                elif action["type"] == "scroll":
                    await page.evaluate("window.scrollBy(0, 600)")
                    await page.wait_for_timeout(1000)

                elif action["type"] == "failed":
                    print(f"[AgentAtlas] ❌ LLM could not determine next action")
                    break

            await browser.close()

        return ExecuteResult(
            site=site, task=task,
            status="partial" if extracted else "failed",
            steps_taken=max_steps, total_tokens=total_tokens,
            data=extracted, history=history,
        )

    # ─────────────────────────────────────────────
    # PRIVATE: LLM decides next action
    # ─────────────────────────────────────────────
    async def _decide_action(self, task, current_url, elements, screenshot_base64, history, extracted) -> tuple[dict, int]:
        history_text   = json.dumps(history[-5:], indent=1) if history else "none"
        extracted_text = json.dumps(extracted, indent=1) if extracted else "none"

        prompt = f"""You are controlling a browser to complete this task: "{task}"

Current URL: {current_url}
Steps taken so far: {history_text}
Data extracted so far: {extracted_text}

Available elements on this page:
{json.dumps(elements, indent=2)}

Look at the screenshot and decide the SINGLE best next action.
Return ONLY valid JSON in one of these formats:

CLICK:   {{"type": "click",   "element": "element_name", "reason": "why"}}
TYPE:    {{"type": "type",    "element": "element_name", "text": "what to type", "reason": "why"}}
EXTRACT: {{"type": "extract", "element": "element_name", "reason": "why"}}
SCROLL:  {{"type": "scroll",  "reason": "why"}}
DONE:    {{"type": "done",    "reason": "why", "data": {{}}}}
FAILED:  {{"type": "failed",  "reason": "why"}}

Rules:
- Only use element names that exist in the available elements above
- If data has already been extracted (check extracted so far), call done immediately
- If task is to extract data and you see the data, use extract then done
- Never extract the same element twice
- Be decisive — pick the single best action
"""
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_base64}", "detail": "low"}},
                    {"type": "text", "text": prompt}
                ]
            }],
            temperature=0,
            response_format={"type": "json_object"},
        )
        tokens = response.usage.total_tokens
        action = json.loads(response.choices[0].message.content.strip())
        return action, tokens

    # ─────────────────────────────────────────────
    # PRIVATE: click action
    # ─────────────────────────────────────────────
    async def _do_click(self, page: Page, action: dict, elements: dict) -> bool:
        element_name = action.get("element", "")
        element_info = elements.get(element_name, {})
        selector     = element_info.get("selector", "")
        sel_type     = element_info.get("type", "")
        try:
            if sel_type == "role" and "+" in selector:
                role, name = selector.split("+", 1)
                await page.get_by_role(role, name=name).first.click(timeout=5000)
                print(f"[AgentAtlas] 👆 Clicked: {element_name} (role)")
                return True
        except Exception:
            pass
        try:
            if sel_type == "text":
                await page.get_by_text(selector, exact=False).first.click(timeout=5000)
                print(f"[AgentAtlas] 👆 Clicked: {element_name} (text)")
                return True
        except Exception:
            pass
        try:
            await page.click(selector, timeout=5000)
            print(f"[AgentAtlas] 👆 Clicked: {element_name} (css)")
            return True
        except Exception as e:
            print(f"[AgentAtlas] ⚠ Click failed for {element_name}: {e}")
            return False

    # ─────────────────────────────────────────────
    # PRIVATE: type action
    # ─────────────────────────────────────────────
    async def _do_type(self, page: Page, action: dict, elements: dict) -> bool:
        element_name = action.get("element", "")
        text         = action.get("text", "")
        element_info = elements.get(element_name, {})
        selector     = element_info.get("selector", "")
        sel_type     = element_info.get("type", "")
        try:
            if sel_type == "role" and "+" in selector:
                role, name = selector.split("+", 1)
                await page.get_by_role(role, name=name).first.fill(text, timeout=5000)
                print(f"[AgentAtlas] ⌨ Typed into: {element_name}")
                return True
        except Exception:
            pass
        try:
            await page.fill(selector, text, timeout=5000)
            print(f"[AgentAtlas] ⌨ Typed into: {element_name} (css)")
            return True
        except Exception as e:
            print(f"[AgentAtlas] ⚠ Type failed for {element_name}: {e}")
            return False

    # ─────────────────────────────────────────────
    # PRIVATE: extract action
    # ─────────────────────────────────────────────
    async def _do_extract(self, page: Page, action: dict, elements: dict) -> dict:
        element_name = action.get("element", "")
        element_info = elements.get(element_name, {})
        selector     = element_info.get("selector", "")
        sel_type     = element_info.get("type", "")
        # try role-based selector
        try:
            if sel_type == "role" and "+" in selector:
                role, name = selector.split("+", 1)
                items = await page.get_by_role(role, name=name).all_text_contents()
                if items:
                    return {element_name: items}
        except Exception:
            pass
        # try css selector
        try:
            if selector and selector.strip():
                items = await page.eval_on_selector_all(
                    selector,
                    "els => els.map(el => ({text: el.innerText?.trim(), href: el.href || null}))"
                )
                if items:
                    return {element_name: items}
        except Exception:
            pass
        # fallback: extract all visible links and headings from page (accessibility tree)
        print(f"[AgentAtlas] 🔄 Selector failed — using accessibility tree fallback")
        try:
            snapshot = await page.accessibility.snapshot(interesting_only=True)
            results = []
            skip_phrases = ["skip to", "join us on", "download", "cookie", "privacy", "legal", "impressum", "newsletter", "instagram", "twitter", "facebook", "linkedin", "app store", "google play", "read more about"]
            def collect(node):
                if not node: return
                role = node.get("role", "")
                name = node.get("name", "").strip()
                name_lower = name.lower()
                if role in ["link", "heading"] and name and len(name) > 3:
                    if not any(skip in name_lower for skip in skip_phrases):
                        results.append({"text": name, "role": role})
                for child in node.get("children", []):
                    collect(child)
            collect(snapshot)
            if results:
                print(f"[AgentAtlas] 📦 Fallback extracted {len(results)} items")
                return {element_name: results}
        except Exception as e:
            print(f"[AgentAtlas] ⚠ Fallback extract failed: {e}")
        return {}

    # ─────────────────────────────────────────────
    # PRIVATE: learn from already-open browser page
    # ─────────────────────────────────────────────
    async def _learn_page_from_browser(self, page: Page, site: str, url: str) -> dict | None:
        try:
            snapshot  = await page.accessibility.snapshot(interesting_only=True)
            acc_nodes = []
            def flatten(node, depth=0):
                if not node:
                    return
                acc_nodes.append({"role": node.get("role",""), "name": node.get("name","")[:100], "value": node.get("value",""), "level": depth})
                for child in node.get("children", []):
                    flatten(child, depth + 1)
            flatten(snapshot)
            if len(acc_nodes) < 5:
                return None
            screenshot_bytes  = await page.screenshot(full_page=False, type="jpeg", quality=50)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            prompt = f"""Build a browser automation schema for {site}. Page: {url}

Return ONLY valid JSON:
{{
  "route_key": "job_list | job_detail | apply_form | search | login | home | other",
  "elements": {{
    "descriptive_name": {{
      "type": "role | aria_label | text | css",
      "selector": "stable selector",
      "confidence": 0.0
    }}
  }}
}}

Rules: confidence >= 0.5, 3-8 elements max, prefer role+name selectors.

Accessibility tree:
{json.dumps(acc_nodes[:100], indent=1)}"""
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_base64}", "detail": "low"}},
                    {"type": "text", "text": prompt}
                ]}],
                temperature=0,
                response_format={"type": "json_object"},
            )
            tokens  = response.usage.total_tokens
            labeled = json.loads(response.choices[0].message.content.strip())
            print(f"[AgentAtlas] 🤖 Learned {len(labeled.get('elements', {}))} elements ({tokens} tokens)")
            return {"route_key": labeled.get("route_key","unknown"), "elements": labeled.get("elements",{}), "tokens_used": tokens, "raw_payload": labeled}
        except Exception as e:
            print(f"[AgentAtlas] Learn error: {e}")
            return None

    # ─────────────────────────────────────────────
    # PRIVATE: crawl new page (launches fresh browser)
    # ─────────────────────────────────────────────
    async def _crawl_page(self, url: str) -> dict:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled","--no-sandbox"])
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}, locale="en-US", timezone_id="America/New_York",
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
                print(f"[AgentAtlas] 📋 Capturing accessibility tree...")
                snapshot  = await page.accessibility.snapshot(interesting_only=True)
                acc_nodes = []
                def flatten(node, depth=0):
                    if not node: return
                    acc_nodes.append({"role": node.get("role",""), "name": node.get("name","")[:100], "value": node.get("value",""), "level": depth})
                    for child in node.get("children",[]): flatten(child, depth+1)
                flatten(snapshot)
                print(f"[AgentAtlas] 📋 {len(acc_nodes)} accessibility nodes captured")
                print(f"[AgentAtlas] 📸 Taking screenshot...")
                await page.unroute("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}")
                await page.wait_for_timeout(1000)
                screenshot_bytes  = await page.screenshot(full_page=False, type="jpeg", quality=60)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                await browser.close()
                return {"acc_nodes": acc_nodes, "screenshot_base64": screenshot_base64}
        except Exception as e:
            print(f"[AgentAtlas] Crawl error: {e}")
            return {}

    # ─────────────────────────────────────────────
    # PRIVATE: learn site (launches fresh browser)
    # ─────────────────────────────────────────────
    async def _learn_site(self, site: str, url: str) -> dict | None:
        crawled           = await self._crawl_page(url)
        acc_nodes         = crawled.get("acc_nodes", [])
        screenshot_base64 = crawled.get("screenshot_base64", "")
        if len(acc_nodes) < 5:
            print(f"[AgentAtlas] ❌ Too few nodes ({len(acc_nodes)}) — page may be blocked")
            return None
        print(f"[AgentAtlas] 🔍 {len(acc_nodes)} nodes + screenshot → GPT-4o Vision...")
        prompt = f"""Build a browser automation schema for {site}. Page: {url}

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

Rules: confidence >= 0.5, 3-8 elements max, prefer role+name selectors, avoid hashed CSS.

Accessibility tree:
{json.dumps(acc_nodes[:100], indent=1)}"""
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_base64}", "detail": "low"}},
                {"type": "text", "text": prompt}
            ]}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        tokens  = response.usage.total_tokens
        labeled = json.loads(response.choices[0].message.content.strip())
        print(f"[AgentAtlas] 🤖 GPT-4o Vision labeled {len(labeled.get('elements', {}))} elements ({tokens} tokens)")
        return {"route_key": labeled.get("route_key","unknown"), "elements": labeled.get("elements",{}), "tokens_used": tokens, "raw_payload": labeled}

    # ─────────────────────────────────────────────
    # PRIVATE: save to registry
    # ─────────────────────────────────────────────
    async def _save_to_registry(self, site: str, url: str, learned: dict):
        try:
            self.sb.table("sites").upsert({"domain": site, "display_name": site}, on_conflict="domain").execute()
            site_id   = self.sb.table("sites").select("id").eq("domain", site).limit(1).execute().data[0]["id"]
            path      = urlparse(url).path or "/"
            route_key = learned.get("route_key", "unknown")
            self.sb.table("page_routes").upsert(
                {"site_id": site_id, "route_key": route_key, "path_pattern": f"^{re.escape(path)}", "example_url": url},
                on_conflict="site_id,route_key"
            ).execute()
            route_id = self.sb.table("page_routes").select("id").eq("site_id", site_id).eq("route_key", route_key).limit(1).execute().data[0]["id"]
            self.sb.table("tasks").upsert({"task_key": "generic_extract", "description": "Generic extraction task"}, on_conflict="task_key").execute()
            task_id  = self.sb.table("tasks").select("id").eq("task_key", "generic_extract").limit(1).execute().data[0]["id"]
            locators = {}
            for purpose, info in learned.get("elements", {}).items():
                if info.get("confidence", 0) >= 0.5:
                    locators[purpose] = [{"type": info.get("type"), "value": info.get("selector"), "priority": 1, "confidence": info.get("confidence")}]
            self.sb.table("playbooks").upsert(
                {"site_id": site_id, "route_id": route_id, "task_id": task_id,
                 "variant_key": "desktop_enUS_loggedout", "version": 1, "status": "active",
                 "confidence": 0.6, "ttl_days": 14,
                 "payload": {"locators": locators, "fingerprint_source": "llm_vision_learned", "source_url": url}},
                on_conflict="site_id,route_id,task_id,variant_key,version"
            ).execute()
        except Exception as e:
            print(f"[AgentAtlas] ⚠ Save failed: {e}")

    # ─────────────────────────────────────────────
    # PRIVATE: match url to route pattern
    # ─────────────────────────────────────────────

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

    # ─────────────────────────────────────────────
    # PRIVATE: build elements dict from payload
    # ─────────────────────────────────────────────
    def _build_elements(self, payload: dict) -> dict:
        locators = payload.get("locators", {})
        elements = {}
        for purpose, locs in locators.items():
            if not locs:
                continue
            best = sorted(locs, key=lambda x: x.get("priority", 99))[0]
            elements[purpose] = {"type": best.get("type"), "selector": best.get("value"), "confidence": best.get("confidence", 0.5)}
        return elements