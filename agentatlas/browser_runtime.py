import base64
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

from agentatlas.models import ExecuteResult, LocatorResolution, ValidationReport
from agentatlas.registry import build_route_fingerprint


class AtlasBrowserRuntimeMixin:
    async def _validate_direct(
        self,
        site: str,
        url: str,
        task_key: str,
        variant_key: str,
        tenant_id: str | None,
        registry_scope: str,
        learn_if_missing: bool,
        persist: bool,
        headless: bool,
        relearn_on_degraded: bool,
    ) -> ValidationReport:
        playbook = self.registry.get_playbook(
            site,
            url,
            task_key=task_key,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        schema = self.registry.fetch_schema(
            site,
            url,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        source = "registry"
        if not schema and learn_if_missing:
            learned_schema = await self.get_schema(
                site,
                url,
                variant_key=variant_key,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            if learned_schema.status == "not_found":
                return ValidationReport(
                    site=site,
                    url=url,
                    route_key="unknown",
                    status="failed",
                    source="not_found",
                    validation_count=1,
                    success_count=0,
                    failure_count=1,
                    success_rate=0.0,
                    last_validated_at=self._now_iso(),
                    schema_version=None,
                    stored_fingerprint=None,
                    current_fingerprint=None,
                    fingerprint_match=None,
                    message="Schema unavailable for validation.",
                )
            schema = {
                "route_key": learned_schema.route_key,
                "confidence": learned_schema.confidence,
                "elements": learned_schema.elements,
            }
            playbook = self.registry.get_playbook(
                site,
                url,
                task_key=task_key,
                variant_key=variant_key,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            source = learned_schema.source

        if not schema:
            return ValidationReport(
                site=site,
                url=url,
                route_key="unknown",
                status="failed",
                source="not_found",
                validation_count=1,
                success_count=0,
                failure_count=1,
                success_rate=0.0,
                last_validated_at=self._now_iso(),
                schema_version=None,
                stored_fingerprint=None,
                current_fingerprint=None,
                fingerprint_match=None,
                message="No schema found in registry.",
            )

        stored_fingerprint = playbook.fingerprint if playbook else None
        schema_version = playbook.schema_version if playbook else None
        locator_results, current_fingerprint = await self._validate_elements(url, schema["elements"], headless=headless)
        success_count = sum(1 for item in locator_results if item.actionable)
        failure_count = len(locator_results) - success_count
        success_rate = success_count / len(locator_results) if locator_results else 0.0
        validation_count = (playbook.validation_count + 1) if playbook else 1
        fingerprint_match = (
            stored_fingerprint == current_fingerprint if stored_fingerprint and current_fingerprint else None
        )
        if fingerprint_match is False:
            status = "stale"
            message = "Route fingerprint changed. Locator set invalidated automatically."
        else:
            status = "healthy" if failure_count == 0 else "degraded"
            message = f"Validated {len(locator_results)} locators."
        report = ValidationReport(
            site=site,
            url=url,
            route_key=schema["route_key"],
            status=status,
            source=source,
            validation_count=validation_count,
            success_count=success_count,
            failure_count=failure_count,
            success_rate=success_rate,
            last_validated_at=self._now_iso(),
            schema_version=schema_version,
            stored_fingerprint=stored_fingerprint,
            current_fingerprint=current_fingerprint,
            fingerprint_match=fingerprint_match,
            locator_results=locator_results,
            message=message,
        )
        if persist:
            try:
                self.registry.persist_validation(
                    site,
                    url,
                    report,
                    task_key=task_key,
                    variant_key=variant_key,
                    tenant_id=tenant_id,
                    registry_scope=registry_scope,
                )
            except Exception as exc:
                report.message = f"{report.message} Persist failed: {exc}"

        if relearn_on_degraded and report.status in {"degraded", "stale"}:
            relearned = await self._learn_site(site, url)
            if relearned:
                relearned = await self._admit_learned_schema(url, relearned)
            if relearned and relearned.get("elements"):
                self.registry.save_schema(
                    site,
                    url,
                    relearned,
                    task_key=task_key,
                    variant_key=variant_key,
                    tenant_id=tenant_id,
                    registry_scope="private" if registry_scope == "private" else "public",
                )
                refreshed_playbook = self.registry.get_playbook(
                    site,
                    url,
                    task_key=task_key,
                    variant_key=variant_key,
                    tenant_id=tenant_id,
                    registry_scope=registry_scope,
                )
                refreshed_schema = self.registry.fetch_schema(
                    site,
                    url,
                    variant_key=variant_key,
                    tenant_id=tenant_id,
                    registry_scope=registry_scope,
                )
                if refreshed_schema:
                    refreshed_results, refreshed_fingerprint = await self._validate_elements(
                        url,
                        refreshed_schema["elements"],
                        headless=headless,
                    )
                    refreshed_success_count = sum(1 for item in refreshed_results if item.actionable)
                    refreshed_failure_count = len(refreshed_results) - refreshed_success_count
                    refreshed_success_rate = refreshed_success_count / len(refreshed_results) if refreshed_results else 0.0
                    refreshed_validation_count = (
                        refreshed_playbook.validation_count + 1 if refreshed_playbook else report.validation_count + 1
                    )
                    refreshed_stored_fingerprint = refreshed_playbook.fingerprint if refreshed_playbook else None
                    refreshed_fingerprint_match = (
                        refreshed_stored_fingerprint == refreshed_fingerprint
                        if refreshed_stored_fingerprint and refreshed_fingerprint
                        else None
                    )
                    if refreshed_fingerprint_match is False:
                        refreshed_status = "stale"
                        refreshed_message = "Automatic relearn completed, but route fingerprint still changed."
                    else:
                        refreshed_status = "healthy" if refreshed_failure_count == 0 else "degraded"
                        refreshed_message = (
                            "Automatic relearn completed and validation reran."
                            if refreshed_status == "healthy"
                            else "Automatic relearn completed, but some locators still failed validation."
                        )
                    report = ValidationReport(
                        site=site,
                        url=url,
                        route_key=refreshed_schema["route_key"],
                        status=refreshed_status,
                        source="llm_learned",
                        validation_count=refreshed_validation_count,
                        success_count=refreshed_success_count,
                        failure_count=refreshed_failure_count,
                        success_rate=refreshed_success_rate,
                        last_validated_at=self._now_iso(),
                        schema_version=refreshed_playbook.schema_version if refreshed_playbook else None,
                        stored_fingerprint=refreshed_stored_fingerprint,
                        current_fingerprint=refreshed_fingerprint,
                        fingerprint_match=refreshed_fingerprint_match,
                        locator_results=refreshed_results,
                        message=refreshed_message,
                    )
                    if persist:
                        try:
                            self.registry.persist_validation(
                                site,
                                url,
                                report,
                                task_key=task_key,
                                variant_key=variant_key,
                                tenant_id=tenant_id,
                                registry_scope=registry_scope,
                            )
                        except Exception as exc:
                            report.message = f"{report.message} Persist failed: {exc}"
            else:
                report.message = f"{report.message} Automatic relearn failed."
        return report

    async def execute(
        self,
        site: str,
        url: str,
        task: str,
        variant: str = "loggedout",
        max_steps: int = 10,
    ) -> ExecuteResult:
        self.logger.info(f"\n[AgentAtlas] 🚀 Executing: {task}")
        self.logger.info(f"[AgentAtlas] 🌐 Starting at: {url}")

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
                self.logger.info(f"\n[AgentAtlas] 📍 Step {step + 1} — {current_url}")

                # Get schema for current page (0 tokens if registry or session cache hit)
                domain = urlparse(current_url).netloc.replace("www.", "")
                cache_key = f"{domain}:{urlparse(current_url).path}"

                if cache_key in self._session_cache:
                    self.logger.info(f"[AgentAtlas] ⚡ Session cache hit — 0 tokens")
                    elements = self._session_cache[cache_key]
                else:
                    schema = self.registry.fetch_schema(domain, current_url)
                    if schema:
                        self.logger.info(f"[AgentAtlas] ✅ Registry hit — 0 tokens")
                        elements = schema["elements"]
                        self._session_cache[cache_key] = elements
                    else:
                        self.logger.info(f"[AgentAtlas] 🔍 Learning current page...")
                        learned = await self._learn_page_from_browser(page, domain, current_url)
                        if learned:
                            self.registry.save_schema(domain, current_url, learned)
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
                self.logger.info(f"[AgentAtlas] 🤖 Action: {action}")
                history.append({"step": step + 1, "url": current_url, "action": action})

                # Detect repeated failures — skip if same action failed 2x
                last_actions = [h["action"] for h in history[-2:]]
                if len(last_actions) == 2 and all(
                    a.get("type") == action.get("type") and 
                    a.get("element") == action.get("element") 
                    for a in last_actions
                ):
                    self.logger.info(f"[AgentAtlas] ⚠ Same action failed twice — skipping and trying next")
                    history.append({"step": step + 1, "url": current_url, "action": {"type": "skip", "reason": "repeated failure"}})
                    continue

                # Execute the action
                if action["type"] == "done":
                    self.logger.info(f"[AgentAtlas] ✅ Task complete!")
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

                elif action["type"] == "select":
                    success = await self._do_select(page, action, elements)
                    if success:
                        await page.wait_for_timeout(500)

                elif action["type"] == "extract":
                    data = await self._do_extract(page, action, elements)
                    if data:
                        extracted.update(data)
                        self.logger.info(f"[AgentAtlas] 📦 Extracted: {list(data.keys())}")

                elif action["type"] == "scroll":
                    await page.evaluate("window.scrollBy(0, 600)")
                    await page.wait_for_timeout(1000)

                elif action["type"] == "failed":
                    self.logger.info(f"[AgentAtlas] ❌ LLM could not determine next action")
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
        history_text   = json.dumps(history[-8:], indent=1) if history else "none"
        # summarize what already succeeded
        done_actions = [h["action"] for h in history if h["action"].get("type") in ["type","click"] and "failed" not in str(h)]
        done_text = json.dumps(done_actions, indent=1) if done_actions else "none"
        extracted_text = json.dumps(extracted, indent=1) if extracted else "none"

        prompt = f"""You are controlling a browser to complete this task: "{task}"

Current URL: {current_url}
Steps taken so far: {history_text}
Data extracted so far: {extracted_text}
Actions already completed successfully: {done_text}

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
- Track completed actions — never repeat a successful type, click or select
- For forms: fill ALL fields before submitting — check history to confirm every field is done
- Only submit when ALL required fields in the task are filled
- If data has already been extracted, call done immediately
- Never repeat the same action twice
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
        reason       = action.get("reason", "")

        # try role-based selector
        try:
            parsed = self._parse_role_selector(selector) if sel_type == "role" else None
            if parsed:
                role, name = parsed
                await page.get_by_role(role, name=name).first.click(timeout=5000)
                self.logger.info(f"[AgentAtlas] 👆 Clicked: {element_name} (role)")
                return True
        except Exception:
            pass

        # try text selector
        try:
            if sel_type == "text" and selector:
                await page.get_by_text(selector, exact=False).first.click(timeout=5000)
                self.logger.info(f"[AgentAtlas] 👆 Clicked: {element_name} (text)")
                return True
        except Exception:
            pass

        # try css selector
        try:
            if selector and selector.strip():
                await page.click(selector, timeout=5000)
                self.logger.info(f"[AgentAtlas] 👆 Clicked: {element_name} (css)")
                return True
        except Exception:
            pass

        # FALLBACK: scan accessibility tree for any link matching element_name or reason
        self.logger.info(f"[AgentAtlas] 🔄 Registry selector failed — scanning page for clickable link...")
        try:
            snapshot = await page.accessibility.snapshot(interesting_only=True)
            candidates = []
            keywords   = [w.lower() for w in (element_name + " " + reason).split() if len(w) > 3]
            skip = ["skip", "login", "cookie", "privacy", "download", "facebook", "twitter", "instagram", "footer", "newsletter", "linkedin", "youtube", "app store", "google play", "read more about", "join us", "amazon jobs home"]

            def scan(node):
                if not node: return
                role = node.get("role", "")
                name = node.get("name", "").strip()
                if role == "link" and name and not any(s in name.lower() for s in skip):
                    score = sum(1 for kw in keywords if kw in name.lower())
                    if score > 0:
                        candidates.append((score, name))
                for child in node.get("children", []):
                    scan(child)
            scan(snapshot)

            if candidates:
                candidates.sort(reverse=True)
                best_name = candidates[0][1]
                self.logger.info(f"[AgentAtlas] 🎯 Found candidate link: {best_name}")
                await page.get_by_role("link", name=best_name).first.click(timeout=5000)
                self.logger.info(f"[AgentAtlas] 👆 Clicked via fallback scan: {best_name}")
                return True

            # Try checkbox FIRST before anything else
            checkbox_words = ["check", "topping", "option", "agree", "accept", "bacon", "cheese", "mushroom", "onion", "select"]
            if any(w in element_name.lower() for w in checkbox_words) or any(w in reason.lower() for w in checkbox_words):
                try:
                    snap = await page.accessibility.snapshot(interesting_only=True)
                    keywords = [w.lower() for w in (element_name + " " + reason).replace("_"," ").split() if len(w) > 2]
                    cb_candidates = []
                    def scan_cb(node):
                        if not node: return
                        role = node.get("role", "")
                        name = node.get("name", "").strip()
                        if role in ["checkbox", "menuitemcheckbox"]:
                            score = sum(1 for kw in keywords if kw in name.lower())
                            if score > 0:
                                cb_candidates.append((score, name))
                        for child in node.get("children", []):
                            scan_cb(child)
                    scan_cb(snap)
                    cb_candidates.sort(reverse=True)
                    if cb_candidates:
                        best = cb_candidates[0][1]
                        self.logger.info(f"[AgentAtlas] 🎯 Found checkbox: {best}")
                        await page.get_by_role("checkbox", name=best).first.check(timeout=5000)
                        self.logger.info(f"[AgentAtlas] ☑ Checked: {best}")
                        return True
                except Exception as e:
                    self.logger.info(f"[AgentAtlas] ⚠ Checkbox fallback failed: {e}")

            # Try submit/button if element name suggests it
            submit_words = ["submit", "send", "order", "apply", "continue", "next", "save", "place"]
            if any(w in element_name.lower() for w in submit_words) or any(w in reason.lower() for w in submit_words):
                self.logger.info(f"[AgentAtlas] 🔄 Trying submit button...")
                try:
                    await page.locator("button[type=submit]").first.click(timeout=5000)
                    self.logger.info(f"[AgentAtlas] 👆 Clicked submit button")
                    return True
                except Exception:
                    pass
                try:
                    await page.locator("input[type=submit]").first.click(timeout=5000)
                    self.logger.info(f"[AgentAtlas] 👆 Clicked input submit")
                    return True
                except Exception:
                    pass
                try:
                    btns = await page.eval_on_selector_all(
                        "button, input[type=submit]",
                        "els => els.map(e => ({text: e.innerText || e.value || '', type: e.type}))")
                    for btn in btns:
                        t = btn.get("text","").strip()
                        if t and t.lower() not in ["cancel", "back", "reset"]:
                            await page.get_by_role("button", name=t).first.click(timeout=3000)
                            self.logger.info(f"[AgentAtlas] 👆 Clicked button: {t}")
                            return True
                except Exception as e:
                    self.logger.info(f"[AgentAtlas] ⚠ Button fallback failed: {e}")

            # Try radio/checkbox by scanning accessibility tree
            self.logger.info(f"[AgentAtlas] 🔄 Trying radio/checkbox fallback...")
            try:
                snap = await page.accessibility.snapshot(interesting_only=True)
                keywords = [w.lower() for w in (element_name + " " + reason).replace("_", " ").split() if len(w) > 2]
                radio_candidates = []
                def scan_inputs(node):
                    if not node: return
                    role = node.get("role", "")
                    name = node.get("name", "").strip()
                    if role in ["radio", "checkbox", "option", "menuitemradio", "menuitemcheckbox"]:
                        score = sum(1 for kw in keywords if kw in name.lower())
                        if score > 0:
                            radio_candidates.append((score, role, name))
                    for child in node.get("children", []):
                        scan_inputs(child)
                scan_inputs(snap)
                radio_candidates.sort(reverse=True)
                if radio_candidates:
                    _, best_role, best_name = radio_candidates[0]
                    self.logger.info(f"[AgentAtlas] 🎯 Found {best_role}: {best_name}")
                    await page.get_by_role(best_role, name=best_name).first.click(timeout=5000)
                    self.logger.info(f"[AgentAtlas] 👆 Clicked {best_role}: {best_name}")
                    return True
            except Exception as e:
                self.logger.info(f"[AgentAtlas] ⚠ Radio/checkbox fallback failed: {e}")

            # Last resort: click first link whose href looks like a job detail URL
            self.logger.info(f"[AgentAtlas] 🔄 No keyword match — trying href pattern fallback...")
            job_url_patterns = ["/jobs/", "/job/", "/careers/", "/position/", "/opening/"]
            links = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(el => ({text: el.innerText.trim(), href: el.href}))"
            )
            for link in links:
                href = link.get("href", "")
                text = link.get("text", "")
                if any(p in href for p in job_url_patterns) and text and len(text) > 3:
                    skip_texts = ["skip", "read more", "login", "cookie"]
                    if not any(s in text.lower() for s in skip_texts):
                        self.logger.info(f"[AgentAtlas] 🎯 Found job link by href: {text} → {href}")
                        await page.goto(href, wait_until="networkidle", timeout=15000)
                        return True
        except Exception as e:
            self.logger.info(f"[AgentAtlas] ⚠ Fallback click failed: {e}")

        self.logger.info(f"[AgentAtlas] ⚠ All click strategies failed for: {element_name}")
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
        normalized_name = element_name.replace("_", " ").lower()

        # Time fields are easy to misclassify in the accessibility fallback.
        # Prefer concrete time inputs before trying generic textbox matching.
        if (
            "time" in normalized_name
            or "hour" in normalized_name
            or "minute" in normalized_name
            or re.fullmatch(r"\d{1,2}:\d{2}", text.strip())
        ):
            time_selectors = [
                "input[type=time]",
                "input[name*='time' i]",
                "input[id*='time' i]",
            ]
            for time_selector in time_selectors:
                try:
                    await page.fill(time_selector, text, timeout=3000)
                    self.logger.info(f"[AgentAtlas] ⌨ Set time via selector: {time_selector}")
                    return True
                except Exception:
                    pass

        # try role+name selector via get_by_role
        try:
            parsed = self._parse_role_selector(selector) if sel_type == "role" else None
            if parsed:
                role, name = parsed
                await page.get_by_role(role, name=name).first.fill(text, timeout=5000)
                self.logger.info(f"[AgentAtlas] ⌨ Typed into: {element_name} (role)")
                return True
        except Exception:
            pass

        # try css selector
        try:
            if selector and selector.strip() and not selector.startswith("textbox["):
                await page.fill(selector, text, timeout=5000)
                self.logger.info(f"[AgentAtlas] ⌨ Typed into: {element_name} (css)")
                return True
        except Exception:
            pass

        # FALLBACK: scan accessibility tree for input matching element_name
        self.logger.info(f"[AgentAtlas] 🔄 Selector failed — scanning for input field...")
        try:
            snapshot  = await page.accessibility.snapshot(interesting_only=True)
            keywords  = [w.lower() for w in normalized_name.split() if len(w) > 2]
            candidates = []
            def scan(node):
                if not node: return
                role = node.get("role", "")
                name = node.get("name", "").strip()
                if role in ["textbox", "searchbox", "combobox", "spinbutton"]:
                    score = sum(1 for kw in keywords if kw in name.lower())
                    if score > 0:
                        candidates.append((score, role, name))
                for child in node.get("children", []):
                    scan(child)
            scan(snapshot)
            candidates.sort(reverse=True)
            if candidates:
                _, best_role, best_name = candidates[0]
                self.logger.info(f"[AgentAtlas] 🎯 Found input: {best_role} — {best_name}")
                # Handle time input (spinbutton Hours/Minutes/AM/PM)
                if best_role == "spinbutton":
                    # spinbutton = time input, use CSS directly
                    try:
                        await page.fill("input[type=time]", text, timeout=3000)
                        self.logger.info(f"[AgentAtlas] ⌨ Set time via CSS: {text}")
                        return True
                    except Exception as te:
                        self.logger.info(f"[AgentAtlas] ⚠ Time CSS fill failed: {te}")
                else:
                    await page.get_by_role(best_role, name=best_name).first.fill(text, timeout=5000)
                    self.logger.info(f"[AgentAtlas] ⌨ Typed into: {best_name}")
                    return True
        except Exception as e:
            self.logger.info(f"[AgentAtlas] ⚠ Type fallback failed: {e}")

        # LAST RESORT: try get_by_label
        try:
            label_guess = element_name.replace("_", " ")
            await page.get_by_label(label_guess, exact=False).first.fill(text, timeout=5000)
            self.logger.info(f"[AgentAtlas] ⌨ Typed via label: {label_guess}")
            return True
        except Exception as e:
            self.logger.info(f"[AgentAtlas] ⚠ Type failed for {element_name}: {e}")
            return False

    # ─────────────────────────────────────────────
    # PRIVATE: select dropdown option
    # ─────────────────────────────────────────────
    async def _do_select(self, page: Page, action: dict, elements: dict) -> bool:
        element_name = action.get("element", "")
        value        = action.get("value", "") or action.get("text", "")
        element_info = elements.get(element_name, {})
        selector     = element_info.get("selector", "")
        sel_type     = element_info.get("type", "")

        # try role-based combobox
        try:
            parsed = self._parse_role_selector(selector) if sel_type == "role" else None
            if parsed:
                role, name = parsed
                await page.get_by_role(role, name=name).first.select_option(label=value, timeout=5000)
                self.logger.info(f"[AgentAtlas] 🔽 Selected: {value} in {element_name} (role)")
                return True
        except Exception:
            pass

        # try css select_option
        try:
            if selector and selector.strip():
                await page.select_option(selector, label=value, timeout=5000)
                self.logger.info(f"[AgentAtlas] 🔽 Selected: {value} in {element_name} (css)")
                return True
        except Exception:
            pass

        # FALLBACK: scan accessibility tree for combobox/listbox matching element name
        self.logger.info(f"[AgentAtlas] 🔄 Scanning for dropdown...")
        try:
            snapshot  = await page.accessibility.snapshot(interesting_only=True)
            keywords  = [w.lower() for w in element_name.replace("_", " ").split() if len(w) > 2]
            candidates = []
            def scan(node):
                if not node: return
                role = node.get("role", "")
                name = node.get("name", "").strip()
                if role in ["combobox", "listbox", "option"]:
                    score = sum(1 for kw in keywords if kw in name.lower())
                    candidates.append((score, role, name))
                for child in node.get("children", []):
                    scan(child)
            scan(snapshot)
            candidates.sort(reverse=True)
            if candidates:
                _, best_role, best_name = candidates[0]
                self.logger.info(f"[AgentAtlas] 🎯 Found dropdown: {best_role} — {best_name}")
                # try select_option by label
                try:
                    await page.get_by_role(best_role, name=best_name).first.select_option(label=value, timeout=5000)
                    self.logger.info(f"[AgentAtlas] 🔽 Selected: {value}")
                    return True
                except Exception:
                    pass
                # try selecting by finding the <select> element near a matching label
                try:
                    await page.get_by_label(best_name, exact=False).first.select_option(label=value, timeout=5000)
                    self.logger.info(f"[AgentAtlas] 🔽 Selected via label: {value}")
                    return True
                except Exception:
                    pass
        except Exception as e:
            self.logger.info(f"[AgentAtlas] ⚠ Dropdown scan failed: {e}")

        # LAST RESORT: try all <select> elements on page
        self.logger.info(f"[AgentAtlas] 🔄 Trying all select elements...")
        try:
            selects = await page.eval_on_selector_all(
                "select",
                "els => els.map(e => ({id: e.id, name: e.name, options: Array.from(e.options).map(o => o.text)}))")
            keywords = [w.lower() for w in element_name.replace("_"," ").split() if len(w) > 2]
            for sel in selects:
                sel_name  = (sel.get("name","") + " " + sel.get("id","")).lower()
                sel_score = sum(1 for kw in keywords if kw in sel_name)
                # check if value is in options
                opts = [o.lower() for o in sel.get("options", [])]
                if value.lower() in opts or sel_score > 0:
                    sel_id = sel.get("name", sel.get("id", ""))
                    locator = "select[name='" + sel_id + "']"
                    await page.select_option(locator, label=value, timeout=5000)
                    self.logger.info(f"[AgentAtlas] 🔽 Selected via select scan: {value}")
                    return True
        except Exception as e:
            self.logger.info(f"[AgentAtlas] ⚠ Select scan failed: {e}")

        self.logger.info(f"[AgentAtlas] ⚠ Dropdown failed for {element_name}")
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
            parsed = self._parse_role_selector(selector) if sel_type == "role" else None
            if parsed:
                role, name = parsed
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
        self.logger.info(f"[AgentAtlas] 🔄 Selector failed — using accessibility tree fallback")
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
                self.logger.info(f"[AgentAtlas] 📦 Fallback extracted {len(results)} items")
                return {element_name: results}
        except Exception as e:
            self.logger.info(f"[AgentAtlas] ⚠ Fallback extract failed: {e}")
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
                dom_summary = await self._collect_dom_summary(page)
                if dom_summary:
                    self.logger.info(f"[AgentAtlas] 🔎 Accessibility tree sparse ({len(acc_nodes)} nodes) — using DOM summary fallback")
                    acc_nodes.extend(dom_summary)
            if len(acc_nodes) < 3:
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

Rules:
- For FORMS: capture EVERY input, select, checkbox, radio, textarea and submit button
- For non-forms: 3-8 most important elements only
- Confidence >= 0.5 only
- Prefer role+name selectors (e.g. role=textbox+Customer name)
- For radio buttons: name each option separately
- For checkboxes: name each option separately
- For submit buttons: always include with type=submit
- Avoid hashed CSS classes

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
            fingerprint = build_route_fingerprint(acc_nodes, url)
            self.logger.info(f"[AgentAtlas] 🤖 Learned {len(labeled.get('elements', {}))} elements ({tokens} tokens)")
            return {
                "route_key": labeled.get("route_key","unknown"),
                "elements": labeled.get("elements",{}),
                "tokens_used": tokens,
                "raw_payload": labeled,
                "fingerprint": fingerprint,
                "fingerprint_source": "accessibility_tree",
            }
        except Exception as e:
            self.logger.info(f"[AgentAtlas] Learn error: {e}")
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
                    self.logger.info(f"[AgentAtlas] 🥷 Stealth mode active")
                await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}", lambda r: r.abort())
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception:
                    pass
                await self._stabilize_page(page)
                for wait_ms in [2000, 2000, 2000]:
                    await page.wait_for_timeout(wait_ms)
                    count = await page.evaluate("() => document.querySelectorAll('a, button').length")
                    self.logger.info(f"[AgentAtlas] ⏳ Interactive elements: {count}")
                    if count > 10:
                        break
                self.logger.info(f"[AgentAtlas] 📋 Capturing accessibility tree...")
                snapshot  = await page.accessibility.snapshot(interesting_only=True)
                acc_nodes = []
                def flatten(node, depth=0):
                    if not node: return
                    acc_nodes.append({"role": node.get("role",""), "name": node.get("name","")[:100], "value": node.get("value",""), "level": depth})
                    for child in node.get("children",[]): flatten(child, depth+1)
                flatten(snapshot)
                self.logger.info(f"[AgentAtlas] 📋 {len(acc_nodes)} accessibility nodes captured")
                if len(acc_nodes) < 5:
                    dom_summary = await self._collect_dom_summary(page)
                    if dom_summary:
                        self.logger.info(f"[AgentAtlas] 🔎 Accessibility tree sparse ({len(acc_nodes)} nodes) — using DOM summary fallback")
                        acc_nodes.extend(dom_summary)
                self.logger.info(f"[AgentAtlas] 📸 Taking screenshot...")
                await page.unroute("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}")
                await page.wait_for_timeout(1000)
                screenshot_bytes  = await page.screenshot(full_page=False, type="jpeg", quality=60)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                await browser.close()
                return {"acc_nodes": acc_nodes, "screenshot_base64": screenshot_base64}
        except Exception as e:
            self.logger.info(f"[AgentAtlas] Crawl error: {e}")
            return {}

    # ─────────────────────────────────────────────
    # PRIVATE: learn site (launches fresh browser)
    # ─────────────────────────────────────────────
    async def _learn_site(self, site: str, url: str) -> dict | None:
        crawled           = await self._crawl_page(url)
        acc_nodes         = crawled.get("acc_nodes", [])
        screenshot_base64 = crawled.get("screenshot_base64", "")
        if len(acc_nodes) < 3:
            self.logger.info(f"[AgentAtlas] ❌ Too few nodes ({len(acc_nodes)}) — page may be blocked")
            return None
        self.logger.info(f"[AgentAtlas] 🔍 {len(acc_nodes)} nodes + screenshot → GPT-4o Vision...")
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
        fingerprint = build_route_fingerprint(acc_nodes, url)
        self.logger.info(f"[AgentAtlas] 🤖 GPT-4o Vision labeled {len(labeled.get('elements', {}))} elements ({tokens} tokens)")
        return {
            "route_key": labeled.get("route_key","unknown"),
            "elements": labeled.get("elements",{}),
            "tokens_used": tokens,
            "raw_payload": labeled,
            "fingerprint": fingerprint,
            "fingerprint_source": "accessibility_tree",
        }

    async def _validate_elements(self, url: str, elements: dict, headless: bool = True) -> tuple[list[LocatorResolution], str | None]:
        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await self._stabilize_page(page)
            await page.wait_for_timeout(1000)
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
            if len(acc_nodes) < 5:
                dom_summary = await self._collect_dom_summary(page)
                if dom_summary:
                    acc_nodes.extend(dom_summary)
            current_fingerprint = build_route_fingerprint(acc_nodes, url) if acc_nodes else None
            for element_name, element_info in elements.items():
                selector_type = element_info.get("type", "")
                selector = element_info.get("selector", "")
                try:
                    locator = self._locator_from_selector(page, selector_type, selector)
                    match_count = await locator.count()
                    visible = False
                    if match_count:
                        try:
                            visible = await locator.first.is_visible(timeout=1000)
                        except Exception:
                            visible = False
                    ambiguous = match_count > 1
                    actionable = match_count == 1 and visible
                    results.append(
                        LocatorResolution(
                            element=element_name,
                            selector_type=selector_type,
                            selector=selector,
                            matched=match_count > 0,
                            visible=visible,
                            match_count=match_count,
                            actionable=actionable,
                            ambiguous=ambiguous,
                        )
                    )
                except Exception as exc:
                    results.append(
                        LocatorResolution(
                            element=element_name,
                            selector_type=selector_type,
                            selector=selector,
                            matched=False,
                            visible=False,
                            match_count=0,
                            actionable=False,
                            ambiguous=False,
                            error=str(exc),
                        )
                    )
            await browser.close()
        return results, current_fingerprint["value"] if current_fingerprint else None

    def _locator_from_selector(self, page: Page, selector_type: str, selector: str):
        if selector_type == "role":
            parsed = self._parse_role_selector(selector)
            if parsed:
                role, name = parsed
                return page.get_by_role(role, name=name)
        if selector_type == "text" and selector:
            return page.get_by_text(selector, exact=False)
        return page.locator(selector)

    def _parse_role_selector(self, selector: str) -> tuple[str, str] | None:
        if not selector:
            return None
        raw = selector.strip()
        if raw.startswith("role="):
            raw = raw[len("role="):].strip()
        if "+" in raw:
            role, name = raw.split("+", 1)
            role = role.strip().lower()
            name = name.strip().strip("'\"")
            return (role, name) if role and name else None
        bracket_match = re.match(r"^\s*([A-Za-z_][\w-]*)\s*\[\s*name\s*=\s*['\"](.+?)['\"]\s*\]\s*$", raw)
        if bracket_match:
            role = bracket_match.group(1).strip().lower()
            name = bracket_match.group(2).strip()
            return (role, name) if role and name else None
        return None

    async def _collect_dom_summary(self, page: Page) -> list[dict]:
        try:
            summary = await page.evaluate(
                """() => {
                    const selectors = [
                        ['h1, h2, h3', 'heading'],
                        ['a[href]', 'link'],
                        ['button', 'button'],
                        ['input, textarea, select', 'input'],
                        ['p', 'text']
                    ];
                    const nodes = [];
                    for (const [selector, role] of selectors) {
                        const elements = Array.from(document.querySelectorAll(selector)).slice(0, 5);
                        for (const el of elements) {
                            const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('id') || '').trim();
                            if (!text) continue;
                            nodes.push({ role, name: text.slice(0, 100), value: '', level: 0 });
                        }
                    }
                    return nodes;
                }"""
            )
            return summary or []
        except Exception:
            return []

    async def _stabilize_page(self, page: Page) -> None:
        await page.wait_for_timeout(500)
        overlay_names = [
            "Accept", "Accept all", "I agree", "Agree", "Continue", "Close", "Dismiss", "Got it",
        ]
        for name in overlay_names:
            try:
                button = page.get_by_role("button", name=name, exact=False).first
                if await button.is_visible(timeout=500):
                    await button.click(timeout=1000)
                    await page.wait_for_timeout(300)
            except Exception:
                pass
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    async def _admit_learned_schema(self, url: str, learned: dict | None) -> dict | None:
        if not learned or not learned.get("elements"):
            return learned
        validation_results, current_fingerprint = await self._validate_elements(
            url,
            learned["elements"],
            headless=True,
        )
        actionable_elements = {}
        dropped_elements = []
        for result in validation_results:
            element = learned["elements"].get(result.element)
            if not element or not result.actionable:
                if result.element in learned["elements"]:
                    dropped_elements.append(result.element)
                continue
            score = self._admission_score_for_locator(element, result)
            if score < 0.6:
                dropped_elements.append(result.element)
                continue
            actionable_elements[result.element] = {
                **element,
                "confidence": round(max(float(element.get("confidence", 0.0) or 0.0), score), 3),
                "admission_score": score,
            }
        learned["elements"] = actionable_elements
        if dropped_elements:
            self.logger.info(
                "agentatlas_locator_admission",
                extra={"url": url, "dropped_elements": dropped_elements, "kept_count": len(actionable_elements)},
            )
        if current_fingerprint:
            fingerprint = learned.get("fingerprint") or {}
            fingerprint["value"] = current_fingerprint
            learned["fingerprint"] = fingerprint
        return learned

    @staticmethod
    def _admission_score_for_locator(element: dict, result: LocatorResolution) -> float:
        strategy_score = AtlasBrowserRuntimeMixin._selector_strategy_score(
            selector_type=element.get("type"),
            selector=element.get("selector"),
        )
        confidence = float(element.get("confidence", 0.0) or 0.0)
        score = (strategy_score * 0.55) + (confidence * 0.30) + 0.15
        if result.ambiguous or result.match_count != 1:
            score -= 0.25
        if not result.visible:
            score -= 0.15
        return round(max(0.0, min(score, 0.99)), 3)

    @staticmethod
    def _selector_strategy_score(selector_type: str | None, selector: str | None) -> float:
        normalized_type = (selector_type or "").strip().lower()
        normalized_selector = (selector or "").strip()
        base_scores = {
            "data_testid": 0.98,
            "aria_label": 0.92,
            "role": 0.88,
            "css": 0.78,
            "text": 0.48,
        }
        score = base_scores.get(normalized_type, 0.4)
        if normalized_type == "css" and normalized_selector.startswith(("input[", "button[", "select[")):
            score += 0.08
        if normalized_type == "text":
            if len(normalized_selector) > 80:
                score -= 0.12
            if len(normalized_selector.split()) > 12:
                score -= 0.08
        if normalized_type == "role" and "+" not in normalized_selector:
            score -= 0.1
        return max(0.0, min(score, 0.99))

    async def _save_to_registry(self, site: str, url: str, learned: dict):
        self.registry.save_schema(site, url, learned)

    async def _fetch_from_registry(self, site: str, url: str) -> dict | None:
        return self.registry.fetch_schema(site, url)

    def _match_route(self, url: str, routes: list) -> dict | None:
        return self.registry.match_route(url, routes)

    def _build_elements(self, payload: dict) -> dict:
        return self.registry.build_elements(payload)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
