"""Main AgentAtlas SDK facade."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

from agentatlas.browser_runtime import AtlasBrowserRuntimeMixin
from agentatlas.client import AtlasHostedClientMixin
from agentatlas.models import PlaybookRecord, SiteSchema, ValidationReport
from agentatlas.registry import (
    AtlasRegistry,
    DEFAULT_REGISTRY_SCOPE,
    DEFAULT_TASK_KEY,
    DEFAULT_VARIANT_KEY,
)
from agentatlas.supabase_client import get_supabase
from agentatlas.versioning import warn_deprecated

load_dotenv()


class Atlas(AtlasHostedClientMixin, AtlasBrowserRuntimeMixin):
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        tenant_id: str | None = None,
        use_api: bool | None = None,
        api_timeout: float = 20.0,
        learn_timeout_seconds: float | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
        logger: logging.Logger | None = None,
    ):
        resolved_api_url = api_url or os.getenv("AGENTATLAS_API_URL")
        self.api_key = api_key or os.getenv("AGENTATLAS_API_KEY")
        self.tenant_id = tenant_id or os.getenv("AGENTATLAS_TENANT_ID")
        self.api_timeout = api_timeout
        self.learn_timeout_seconds = (
            float(os.getenv("AGENTATLAS_LEARN_TIMEOUT_SECONDS", "25"))
            if learn_timeout_seconds is None
            else float(learn_timeout_seconds)
        )
        self.registry_scope = registry_scope or os.getenv("AGENTATLAS_REGISTRY_SCOPE", DEFAULT_REGISTRY_SCOPE)
        self.api_url = resolved_api_url.rstrip("/") if resolved_api_url else None
        self.use_api = bool(self.api_url) if use_api is None else use_api
        if self.use_api and not self.api_url:
            raise ValueError("Hosted client mode requires api_url or AGENTATLAS_API_URL.")

        self.logger = logger or logging.getLogger("agentatlas")
        self.sb = None
        self.registry = None
        self.client = None
        if not self.use_api:
            self.sb = get_supabase()
            self.registry = AtlasRegistry(self.sb)
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._session_cache = {}

    async def get_schema(
        self,
        site: str,
        url: str,
        variant_key: str | None = None,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
        max_learn_seconds: float | None = None,
    ) -> SiteSchema:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        if self.use_api:
            return self._get_schema_via_api(
                site=site,
                url=url,
                variant_key=resolved_variant_key,
                registry_scope=resolved_registry_scope,
                max_learn_seconds=max_learn_seconds,
            )

        self.logger.info("agentatlas_schema_lookup", extra={"site": site, "url": url, "variant_key": resolved_variant_key})
        schema = self.registry.fetch_schema(
            site,
            url,
            variant_key=resolved_variant_key,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )
        if schema:
            self.logger.info("agentatlas_registry_hit", extra={"site": site, "url": url, "variant_key": resolved_variant_key})
            return SiteSchema(
                site=site,
                url=url,
                route_key=schema["route_key"],
                status="found",
                confidence=schema["confidence"],
                elements=schema["elements"],
                source="registry",
                tokens_used=0,
                message="Schema found in registry. No LLM used.",
            )

        snapshot = self.registry.get_route_playbook_snapshot(
            site=site,
            url=url,
            task_key=DEFAULT_TASK_KEY,
            variant_key=resolved_variant_key,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )
        route_key = (snapshot or {}).get("route_key") or "unknown"
        snapshot_payload = (snapshot or {}).get("payload") or {}
        promotion = snapshot_payload.get("promotion") or {}
        registry_meta = snapshot_payload.get("registry") or {}
        if (
            promotion.get("review_status") == "review_required"
            and registry_meta.get("scope", "public") == "public"
        ):
            return SiteSchema(
                site=site,
                url=url,
                route_key=route_key,
                status="pending_review",
                confidence=float((snapshot or {}).get("confidence") or 0.0),
                elements={},
                source="review_queue",
                tokens_used=0,
                message="Schema exists in the public registry but is awaiting review approval.",
                recovery_state="review_required",
            )
        if getattr(self.registry, "read_degraded", lambda: False)():
            return SiteSchema(
                site=site,
                url=url,
                route_key=route_key,
                status="registry_unavailable",
                confidence=0.0,
                elements={},
                source="registry_unavailable",
                tokens_used=0,
                message="Registry read degraded. Skipping cold start until backing store recovers.",
                recovery_state="degraded_read",
            )
        recovery_reason = "stale" if (snapshot or {}).get("status") == "stale" else "cold_start"
        acquired, lease = self.registry.start_recovery(
            site=site,
            route_key=route_key,
            task_key=DEFAULT_TASK_KEY,
            variant_key=resolved_variant_key,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
            reason=recovery_reason,
        )
        if not acquired:
            recovery_state = "relearning" if lease.get("reason") == "stale" else "learning"
            return SiteSchema(
                site=site,
                url=url,
                route_key=route_key,
                status="recovering",
                confidence=0.0,
                elements={},
                source="recovery_pending",
                tokens_used=0,
                message=(
                    "Schema recovery already in progress for this route."
                    if recovery_state == "relearning"
                    else "Cold-start learning already in progress for this route."
                ),
                recovery_state=recovery_state,
            )

        self.logger.info("agentatlas_schema_cold_start", extra={"site": site, "url": url, "variant_key": resolved_variant_key})
        try:
            learn_budget = self.learn_timeout_seconds if max_learn_seconds is None else float(max_learn_seconds)
            try:
                learned = await asyncio.wait_for(self._learn_site(site, url), timeout=learn_budget)
            except asyncio.TimeoutError:
                self.logger.warning(
                    "agentatlas_schema_learn_timeout",
                    extra={"site": site, "url": url, "variant_key": resolved_variant_key, "timeout_seconds": learn_budget},
                )
                return SiteSchema(
                    site=site,
                    url=url,
                    route_key=route_key,
                    status="timeout",
                    confidence=0.0,
                    elements={},
                    source="timeout",
                    tokens_used=0,
                    message=f"Schema learning timed out after {learn_budget:.1f}s.",
                    recovery_state="timed_out",
                )
            if not learned:
                return SiteSchema(
                    site=site,
                    url=url,
                    route_key=route_key,
                    status="not_found",
                    confidence=0.0,
                    elements={},
                    source="not_found",
                    tokens_used=0,
                    message="Could not learn site. Page may be blocked or empty.",
                    recovery_state="failed",
                )
            learned = await self._admit_learned_schema(url, learned)
            if not learned or not learned.get("elements"):
                return SiteSchema(
                    site=site,
                    url=url,
                    route_key=learned["route_key"] if learned else route_key,
                    status="not_found",
                    confidence=0.0,
                    elements={},
                    source="not_found",
                    tokens_used=learned.get("tokens_used", 0) if learned else 0,
                    message="Learned schema did not produce any actionable locators.",
                    recovery_state="failed",
                )
            self.registry.save_schema(
                site,
                url,
                learned,
                variant_key=resolved_variant_key,
                tenant_id=resolved_tenant_id,
                registry_scope="private" if resolved_registry_scope == "private" else "public",
            )
            self.logger.info("agentatlas_schema_saved", extra={"site": site, "url": url, "variant_key": resolved_variant_key})
            return SiteSchema(
                site=site,
                url=url,
                route_key=learned["route_key"],
                status="learned",
                confidence=0.6,
                elements=learned["elements"],
                source="llm_learned",
                tokens_used=learned["tokens_used"],
                message=f"Schema learned and saved. Tokens used: {learned['tokens_used']}.",
                recovery_state="completed",
            )
        finally:
            self.registry.finish_recovery(lease)

    async def get_playbook(
        self,
        site: str,
        url: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str | None = None,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
    ) -> PlaybookRecord | None:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        if self.use_api:
            return self._resolve_schema_via_api(
                site=site,
                url=url,
                task_key=task_key,
                variant_key=resolved_variant_key,
                registry_scope=resolved_registry_scope,
            ).playbook
        return self.registry.get_playbook(
            site,
            url,
            task_key=task_key,
            variant_key=resolved_variant_key,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )

    async def resolve_locator(
        self,
        site: str,
        url: str,
        element_name: str,
        variant_key: str | None = None,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
    ) -> dict | None:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        if self.use_api:
            return self._resolve_locator_via_api(
                site=site,
                url=url,
                element_name=element_name,
                variant_key=resolved_variant_key,
                registry_scope=resolved_registry_scope,
            ).locator
        return self.registry.resolve_locator(
            site,
            url,
            element_name,
            variant_key=resolved_variant_key,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )

    async def record_outcome(
        self,
        site: str,
        url: str,
        status: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str | None = None,
        metadata: dict | None = None,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
    ) -> bool:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        if self.use_api:
            return self._record_outcome_via_api(
                site=site,
                url=url,
                status=status,
                task_key=task_key,
                variant_key=resolved_variant_key,
                registry_scope=resolved_registry_scope,
                metadata=metadata,
            )
        return self.registry.record_outcome(
            site,
            url,
            status=status,
            task_key=task_key,
            variant_key=resolved_variant_key,
            metadata=metadata,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )

    async def validate(
        self,
        site: str,
        url: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str | None = None,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
        learn_if_missing: bool = True,
        persist: bool = True,
        headless: bool = True,
        relearn_on_degraded: bool = True,
    ) -> ValidationReport:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        if self.use_api:
            return self._validate_via_api(
                site=site,
                url=url,
                task_key=task_key,
                variant_key=resolved_variant_key,
                registry_scope=resolved_registry_scope,
                learn_if_missing=learn_if_missing,
                persist=persist,
                headless=headless,
                relearn_on_degraded=relearn_on_degraded,
            )
        return await self._validate_direct(
            site=site,
            url=url,
            task_key=task_key,
            variant_key=resolved_variant_key,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
            learn_if_missing=learn_if_missing,
            persist=persist,
            headless=headless,
            relearn_on_degraded=relearn_on_degraded,
        )

    async def list_review_queue(self, limit: int = 50) -> list[dict]:
        scope = self.registry_scope if self.registry_scope != "auto" else "public"
        if self.use_api:
            return [item.__dict__ for item in self._list_review_queue_via_api(limit=limit, registry_scope=scope)]
        return self.registry.list_review_queue(
            tenant_id=self.tenant_id,
            registry_scope=scope,
            limit=limit,
        )

    async def get_review_dashboard(self, limit: int = 100) -> dict:
        scope = self.registry_scope if self.registry_scope != "auto" else "public"
        if self.use_api:
            return self._get_review_dashboard_via_api(limit=limit, registry_scope=scope)
        return self.registry.get_review_dashboard(
            tenant_id=self.tenant_id,
            registry_scope=scope,
            limit=limit,
        )

    async def list_review_audit(self, limit: int = 100) -> list[dict]:
        scope = self.registry_scope if self.registry_scope != "auto" else "auto"
        if self.use_api:
            return [item.__dict__ for item in self._list_review_audit_via_api(limit=limit, registry_scope=scope)]
        return self.registry.list_review_audit(
            tenant_id=self.tenant_id,
            registry_scope=scope,
            limit=limit,
        )

    async def promote_playbook(self, playbook_id, reviewer: str, approved: bool = True, notes: str = "") -> bool:
        if self.use_api:
            return self._promote_playbook_via_api(
                playbook_id=playbook_id,
                reviewer=reviewer,
                approved=approved,
                notes=notes,
            )
        return self.registry.promote_playbook(
            playbook_id=playbook_id,
            reviewer=reviewer,
            approved=approved,
            notes=notes,
        )

    async def flag_schema(
        self,
        site: str,
        url: str,
        reporter: str,
        reason: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str | None = None,
        registry_scope: str | None = None,
        notes: str = "",
        metadata: dict | None = None,
    ) -> bool:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        resolved_registry_scope = registry_scope or self.registry_scope
        if self.use_api:
            return self._flag_schema_via_api(
                site=site,
                url=url,
                reporter=reporter,
                reason=reason,
                task_key=task_key,
                variant_key=resolved_variant_key,
                registry_scope=resolved_registry_scope,
                notes=notes,
                metadata=metadata,
            )
        return self.registry.flag_schema(
            site=site,
            url=url,
            reporter=reporter,
            reason=reason,
            task_key=task_key,
            variant_key=resolved_variant_key,
            tenant_id=self.tenant_id,
            registry_scope=resolved_registry_scope,
            notes=notes,
            metadata=metadata,
        )

    async def get_route_scope_diff(
        self,
        site: str,
        url: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str | None = None,
    ) -> dict | None:
        resolved_variant_key = self.infer_variant_key(url=url, variant_key=variant_key)
        if self.use_api:
            return self._get_route_scope_diff_via_api(
                site=site,
                url=url,
                task_key=task_key,
                variant_key=resolved_variant_key,
            ).__dict__
        return self.registry.get_route_scope_diff(
            site=site,
            url=url,
            task_key=task_key,
            variant_key=resolved_variant_key,
            tenant_id=self.tenant_id,
        )

    def list_revalidation_candidates(
        self,
        max_age_hours: int = 24,
        limit: int = 25,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
    ) -> list[dict]:
        self._require_direct_mode("list_revalidation_candidates")
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        return self.registry.list_revalidation_candidates(
            max_age_hours=max_age_hours,
            limit=limit,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )

    async def run_revalidation_cycle(
        self,
        max_age_hours: int = 24,
        limit: int = 25,
        headless: bool = True,
        tenant_id: str | None = None,
        registry_scope: str | None = None,
    ) -> list[dict]:
        self._require_direct_mode("run_revalidation_cycle")
        resolved_tenant_id = self.tenant_id if tenant_id is None else tenant_id
        resolved_registry_scope = registry_scope or self.registry_scope
        candidates = self.registry.list_revalidation_candidates(
            max_age_hours=max_age_hours,
            limit=limit,
            tenant_id=resolved_tenant_id,
            registry_scope=resolved_registry_scope,
        )
        results = []
        for candidate in candidates:
            report = await self.validate(
                site=candidate["site"],
                url=candidate["url"],
                variant_key=candidate.get("variant_key"),
                tenant_id=resolved_tenant_id,
                registry_scope=resolved_registry_scope,
                learn_if_missing=False,
                persist=True,
                headless=headless,
                relearn_on_degraded=True,
            )
            results.append(
                {
                    "playbook_id": candidate["playbook_id"],
                    "site": candidate["site"],
                    "url": candidate["url"],
                    "variant_key": candidate.get("variant_key"),
                    "revalidation_reason": candidate.get("revalidation_reason"),
                    "status": report.status,
                    "success_rate": report.success_rate,
                    "last_validated_at": report.last_validated_at,
                }
            )
        return results

    async def execute(self, *args, **kwargs):
        warn_deprecated(
            "Atlas.execute() is deprecated and removed from the stable Atlas surface. "
            "Use agentatlas.executor.AgentExecutor.execute() instead.",
            stacklevel=2,
        )
        raise RuntimeError(
            "Atlas.execute() has been demoted from the main SDK surface. "
            "Use agentatlas.executor.AgentExecutor for browser execution tooling."
        )
