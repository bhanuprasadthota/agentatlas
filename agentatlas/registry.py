import copy
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse

from agentatlas.models import PlaybookRecord, ValidationReport
from agentatlas.registry_benchmarks import AtlasBenchmarkMixin
from agentatlas.registry_common import (
    DEFAULT_REGISTRY_SCOPE,
    DEFAULT_TASK_KEY,
    DEFAULT_VARIANT_KEY,
    build_route_fingerprint,
    now_iso,
    path_pattern_from_signature,
    path_signature,
)
from agentatlas.registry_quality import AtlasQualityMixin
from agentatlas.registry_review import AtlasReviewMixin


class AtlasRegistry(AtlasReviewMixin, AtlasBenchmarkMixin, AtlasQualityMixin):
    _recovery_lock = threading.Lock()
    _recovery_leases: dict[str, dict] = {}

    def __init__(self, supabase_client):
        self.sb = supabase_client
        self._cache_ttl_seconds = int(os.getenv("AGENTATLAS_REGISTRY_CACHE_TTL_SECONDS", "300"))
        self._cache = {
            "schema": {},
            "playbook": {},
            "snapshot": {},
        }
        self._read_failure_count = 0
        self._read_failure_threshold = int(os.getenv("AGENTATLAS_REGISTRY_FAILURE_THRESHOLD", "3"))
        self._read_cooldown_seconds = int(os.getenv("AGENTATLAS_REGISTRY_COOLDOWN_SECONDS", "30"))
        self._read_circuit_open_until = 0.0
        self._last_read_degraded = False

    def read_degraded(self) -> bool:
        return self._last_read_degraded

    def _cache_lookup(self, bucket: str, key: str, allow_stale: bool = False):
        entry = self._cache.get(bucket, {}).get(key)
        if not entry:
            return None
        if allow_stale or entry["expires_at"] > time.time():
            return copy.deepcopy(entry["value"])
        self._cache[bucket].pop(key, None)
        return None

    def _cache_store(self, bucket: str, key: str, value):
        self._cache.setdefault(bucket, {})[key] = {
            "value": copy.deepcopy(value),
            "expires_at": time.time() + self._cache_ttl_seconds,
        }

    def _read_cache_key(self, *parts) -> str:
        return "::".join("" if part is None else str(part) for part in parts)

    def _read_circuit_open(self) -> bool:
        return self._read_circuit_open_until > time.time()

    def _mark_read_success(self) -> None:
        self._read_failure_count = 0
        self._read_circuit_open_until = 0.0
        self._last_read_degraded = False

    def _mark_read_failure(self) -> None:
        self._read_failure_count += 1
        self._last_read_degraded = True
        if self._read_failure_count >= self._read_failure_threshold:
            self._read_circuit_open_until = time.time() + self._read_cooldown_seconds

    def _run_read(self, bucket: str, key: str, loader):
        self._last_read_degraded = False
        if self._read_circuit_open():
            cached = self._cache_lookup(bucket, key, allow_stale=True)
            self._last_read_degraded = True
            return cached
        try:
            value = loader()
            self._mark_read_success()
            if value is not None:
                self._cache_store(bucket, key, value)
            return copy.deepcopy(value)
        except Exception:
            self._mark_read_failure()
            return self._cache_lookup(bucket, key, allow_stale=True)

    @classmethod
    def _recovery_key(
        cls,
        site: str,
        route_key: str,
        task_key: str,
        variant_key: str,
        tenant_id: str | None,
        registry_scope: str,
    ) -> str:
        return "::".join(
            [
                site,
                route_key,
                task_key,
                variant_key,
                tenant_id or "public",
                registry_scope or DEFAULT_REGISTRY_SCOPE,
            ]
        )

    @classmethod
    def start_recovery(
        cls,
        *,
        site: str,
        route_key: str,
        task_key: str,
        variant_key: str,
        tenant_id: str | None,
        registry_scope: str,
        reason: str,
        ttl_seconds: int = 300,
    ) -> tuple[bool, dict]:
        key = cls._recovery_key(site, route_key, task_key, variant_key, tenant_id, registry_scope)
        now = time.time()
        with cls._recovery_lock:
            existing = cls._recovery_leases.get(key)
            if existing and existing.get("expires_at", 0) > now:
                return False, dict(existing)
            lease = {
                "key": key,
                "owner": uuid.uuid4().hex,
                "reason": reason,
                "started_at": now,
                "expires_at": now + ttl_seconds,
            }
            cls._recovery_leases[key] = lease
            return True, dict(lease)

    @classmethod
    def finish_recovery(cls, lease: dict | None) -> None:
        if not lease:
            return
        key = lease.get("key")
        owner = lease.get("owner")
        if not key or not owner:
            return
        with cls._recovery_lock:
            current = cls._recovery_leases.get(key)
            if current and current.get("owner") == owner:
                cls._recovery_leases.pop(key, None)

    def get_route_playbook_snapshot(
        self,
        site: str,
        url: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
    ) -> dict | None:
        cache_key = self._read_cache_key(site, url, task_key, variant_key, tenant_id, registry_scope)

        def loader():
            route = self._find_route(site, url)
            if not route:
                return None
            task_rows = (
                self.sb.table("tasks")
                .select("id")
                .eq("task_key", task_key)
                .limit(1)
                .execute()
                .data
            )
            if not task_rows:
                return {"route_key": route["route_key"], "status": None, "payload": {}}
            playbooks = (
                self.sb.table("playbooks")
                .select("id, payload, confidence, variant_key, version, status")
                .eq("site_id", route["site_id"])
                .eq("route_id", route["id"])
                .eq("task_id", task_rows[0]["id"])
                .eq("variant_key", variant_key)
                .order("version", desc=True)
                .limit(25)
                .execute()
                .data
            )
            if not playbooks:
                return {"route_key": route["route_key"], "status": None, "payload": {}}
            scoped = self._filter_playbooks_by_scope(
                playbooks=playbooks,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            if not scoped:
                return {"route_key": route["route_key"], "status": None, "payload": {}}
            latest = scoped[0]
            return {
                "route_key": route["route_key"],
                "status": latest.get("status"),
                "payload": latest.get("payload") or {},
                "version": latest.get("version"),
                "confidence": latest.get("confidence"),
            }

        return self._run_read("snapshot", cache_key, loader)

    def fetch_schema(
        self,
        site: str,
        url: str,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
    ) -> dict | None:
        cache_key = self._read_cache_key(site, url, variant_key, tenant_id, registry_scope)

        def loader():
            route = self._find_route(site, url)
            if not route:
                return None
            playbooks = (
                self.sb.table("playbooks")
                .select("payload, confidence, variant_key, version")
                .eq("site_id", route["site_id"])
                .eq("route_id", route["id"])
                .eq("status", "active")
                .order("confidence", desc=True)
                .limit(25)
                .execute()
                .data
            )
            if not playbooks:
                return None
            scoped_playbooks = self._filter_playbooks_by_scope(
                playbooks=playbooks,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            ranked_playbooks = self._resolve_scope_conflicts(
                playbooks=scoped_playbooks,
                variant_key=variant_key,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            for playbook in ranked_playbooks:
                payload = playbook.get("payload") or {}
                quality = self._compute_quality_summary(
                    confidence=playbook.get("confidence", 0.0),
                    validation=payload.get("validation", {}),
                    telemetry=payload.get("telemetry", {}),
                    promotion=payload.get("promotion", {}),
                    registry=payload.get("registry", {}),
                )
                if not quality.get("serveable", True):
                    continue
                elements = self.build_elements(payload)
                if not elements:
                    continue
                return {
                    "route_key": route["route_key"],
                    "confidence": playbook["confidence"],
                    "elements": elements,
                    "quality": quality,
                    "variant_key": playbook.get("variant_key"),
                }
            return None

        return self._run_read("schema", cache_key, loader)

    def get_playbook(
        self,
        site: str,
        url: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
    ) -> PlaybookRecord | None:
        cache_key = self._read_cache_key(site, url, task_key, variant_key, tenant_id, registry_scope)

        def loader():
            route = self._find_route(site, url)
            if not route:
                return None
            task_rows = (
                self.sb.table("tasks")
                .select("id")
                .eq("task_key", task_key)
                .limit(1)
                .execute()
                .data
            )
            if not task_rows:
                return None
            playbooks = (
                self.sb.table("playbooks")
                .select("payload, confidence, variant_key, version")
                .eq("site_id", route["site_id"])
                .eq("route_id", route["id"])
                .eq("task_id", task_rows[0]["id"])
                .eq("status", "active")
                .order("version", desc=True)
                .limit(25)
                .execute()
                .data
            )
            if not playbooks:
                return None
            scoped_playbooks = self._filter_playbooks_by_scope(
                playbooks=playbooks,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            ranked_playbooks = self._resolve_scope_conflicts(
                playbooks=scoped_playbooks,
                variant_key=variant_key,
                tenant_id=tenant_id,
                registry_scope=registry_scope,
            )
            if not ranked_playbooks:
                return None
            selected = ranked_playbooks[0]
            payload = selected["payload"] or {}
            validation = self._get_latest_validation_summary(
                playbook_payload=payload,
                site_id=route["site_id"],
                route_id=route["id"],
                task_id=task_rows[0]["id"],
                variant_key=selected.get("variant_key", variant_key),
            )
            quality = self._compute_quality_summary(
                confidence=selected["confidence"],
                validation=validation,
                telemetry=payload.get("telemetry", {}),
                promotion=payload.get("promotion", {}),
                registry=payload.get("registry", {}),
            )
            payload["quality"] = quality
            registry_meta = payload.get("registry", {})
            promotion = payload.get("promotion", {})
            return PlaybookRecord(
                site=site,
                url=url,
                route_key=route["route_key"],
                task_key=task_key,
                variant_key=selected.get("variant_key", variant_key),
                confidence=selected["confidence"],
                elements=self.build_elements(payload),
                source=payload.get("fingerprint_source", "registry"),
                schema_version=selected.get("version", 1),
                fingerprint=(payload.get("fingerprint") or {}).get("value"),
                last_validated_at=validation.get("last_validated_at"),
                success_rate=validation.get("success_rate"),
                validation_count=validation.get("validation_count", 0),
                trust_score=quality.get("trust_score"),
                quality_status=quality.get("quality_status", "candidate"),
                serveable=quality.get("serveable", True),
                registry_scope=registry_meta.get("scope", "public"),
                tenant_id=registry_meta.get("tenant_id"),
                review_status=promotion.get("review_status", "approved"),
                metadata=payload,
            )

        return self._run_read("playbook", cache_key, loader)

    def resolve_locator(
        self,
        site: str,
        url: str,
        element_name: str,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
    ) -> dict | None:
        schema = self.fetch_schema(
            site,
            url,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        if not schema:
            return None
        return schema["elements"].get(element_name)

    def save_schema(
        self,
        site: str,
        url: str,
        learned: dict,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = "public",
    ) -> None:
        if registry_scope == "private" and not tenant_id:
            raise ValueError("Private registry scope requires a tenant_id.")
        self.sb.table("sites").upsert(
            {"domain": site, "display_name": site},
            on_conflict="domain",
        ).execute()
        site_id = (
            self.sb.table("sites")
            .select("id")
            .eq("domain", site)
            .limit(1)
            .execute()
            .data[0]["id"]
        )
        route_key = learned.get("route_key", "unknown")
        fingerprint = learned.get("fingerprint") or {}
        signature = fingerprint.get("path_signature") or path_signature(urlparse(url).path or "/")
        self.sb.table("page_routes").upsert(
            {
                "site_id": site_id,
                "route_key": route_key,
                "path_pattern": path_pattern_from_signature(signature),
                "example_url": url,
            },
            on_conflict="site_id,route_key",
        ).execute()
        route_id = (
            self.sb.table("page_routes")
            .select("id")
            .eq("site_id", site_id)
            .eq("route_key", route_key)
            .limit(1)
            .execute()
            .data[0]["id"]
        )
        self.sb.table("tasks").upsert(
            {"task_key": task_key, "description": "Generic extraction task"},
            on_conflict="task_key",
        ).execute()
        task_id = (
            self.sb.table("tasks")
            .select("id")
            .eq("task_key", task_key)
            .limit(1)
            .execute()
            .data[0]["id"]
        )
        locators = {}
        for purpose, info in learned.get("elements", {}).items():
            normalized = self._normalize_element_locator(info)
            if normalized and normalized.get("confidence", 0) >= 0.6:
                locators[purpose] = [{
                    "type": normalized.get("type"),
                    "value": normalized.get("selector"),
                    "priority": self._locator_priority(normalized),
                    "confidence": normalized.get("confidence"),
                }]
        payload = {
            "locators": locators,
            "fingerprint_source": learned.get("fingerprint_source", "llm_vision_learned"),
            "fingerprint": learned.get("fingerprint"),
            "source_url": url,
            "registry": {
                "scope": registry_scope,
                "tenant_id": tenant_id,
            },
            "validation": {
                "last_validated_at": None,
                "validation_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "success_rate": None,
            },
        }
        payload["promotion"] = self._build_promotion_state(
            site=site,
            registry_scope=registry_scope,
            tenant_id=tenant_id,
        )
        payload["quality"] = self._compute_quality_summary(
            confidence=0.6,
            validation=payload["validation"],
            telemetry={},
            promotion=payload["promotion"],
            registry=payload["registry"],
        )
        existing_rows = (
            self.sb.table("playbooks")
            .select("id, version, status, payload, confidence")
            .eq("site_id", site_id)
            .eq("route_id", route_id)
            .eq("task_id", task_id)
            .eq("variant_key", variant_key)
            .order("version", desc=True)
            .limit(25)
            .execute()
            .data
        )
        scoped_existing_rows = self._filter_playbooks_by_scope(
            existing_rows or [],
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        latest = scoped_existing_rows[0] if scoped_existing_rows else None
        latest_payload = latest.get("payload") if latest else None
        latest_fingerprint = self._fingerprint_value(latest_payload)
        new_fingerprint = self._fingerprint_value(payload)

        if latest and latest_fingerprint == new_fingerprint:
            payload["validation"] = (latest_payload or {}).get("validation", payload["validation"])
            payload["telemetry"] = (latest_payload or {}).get("telemetry", {})
            payload["promotion"] = (latest_payload or {}).get("promotion", payload["promotion"])
            payload["quality"] = self._compute_quality_summary(
                confidence=latest.get("confidence", 0.6),
                validation=payload["validation"],
                telemetry=payload.get("telemetry", {}),
                promotion=payload.get("promotion", {}),
                registry=payload.get("registry", {}),
            )
            self.sb.table("playbooks").update({
                "payload": payload,
                "confidence": payload["quality"]["trust_score"],
                "status": "active",
            }).eq("id", latest["id"]).execute()
            return

        if latest:
            self.sb.table("playbooks").update({"status": "stale"}).eq("id", latest["id"]).execute()

        next_version = existing_rows[0].get("version", 0) + 1 if existing_rows else 1
        self.sb.table("playbooks").insert(
            {
                "site_id": site_id,
                "route_id": route_id,
                "task_id": task_id,
                "variant_key": variant_key,
                "version": next_version,
                "status": "active",
                "confidence": payload["quality"]["trust_score"],
                "ttl_days": 14,
                "payload": payload,
            }
        ).execute()

    def persist_validation(
        self,
        site: str,
        url: str,
        report: ValidationReport,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
    ) -> bool:
        route = self._find_route(site, url)
        if not route:
            return False
        task_rows = (
            self.sb.table("tasks")
            .select("id")
            .eq("task_key", task_key)
            .limit(1)
            .execute()
            .data
        )
        if not task_rows:
            return False
        playbook = self.get_playbook(
            site=site,
            url=url,
            task_key=task_key,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        if not playbook:
            return False
        playbook_rows = (
            self.sb.table("playbooks")
            .select("id, payload, confidence, variant_key")
            .eq("site_id", route["site_id"])
            .eq("route_id", route["id"])
            .eq("task_id", task_rows[0]["id"])
            .eq("variant_key", playbook.variant_key)
            .eq("status", "active")
            .order("version", desc=True)
            .limit(25)
            .execute()
            .data
        )
        scoped_rows = self._filter_playbooks_by_scope(playbook_rows, tenant_id=tenant_id, registry_scope=registry_scope)
        if not scoped_rows:
            return False
        selected_row = scoped_rows[0]
        payload = selected_row.get("payload") or {}
        validation_summary = {
            "last_validated_at": report.last_validated_at,
            "validation_count": report.validation_count,
            "success_count": report.success_count,
            "failure_count": report.failure_count,
            "success_rate": report.success_rate,
            "status": report.status,
            "schema_version": report.schema_version,
            "stored_fingerprint": report.stored_fingerprint,
            "current_fingerprint": report.current_fingerprint,
            "fingerprint_match": report.fingerprint_match,
            "message": report.message,
            "locator_results": [
                {
                    "element": item.element,
                    "selector_type": item.selector_type,
                    "selector": item.selector,
                    "matched": item.matched,
                    "visible": item.visible,
                    "match_count": item.match_count,
                    "actionable": item.actionable,
                    "ambiguous": item.ambiguous,
                    "error": item.error,
                }
                for item in report.locator_results
            ],
        }
        payload["validation"] = validation_summary
        payload["quality"] = self._compute_quality_summary(
            confidence=selected_row.get("confidence", 0.0),
            validation=validation_summary,
            telemetry=payload.get("telemetry", {}),
            promotion=payload.get("promotion", {}),
            registry=payload.get("registry", {}),
        )
        self._insert_validation_history(
            playbook_id=selected_row["id"],
            site_id=route["site_id"],
            route_id=route["id"],
            task_id=task_rows[0]["id"],
            variant_key=playbook.variant_key,
            report=report,
        )
        playbook_update = {"payload": payload, "confidence": payload["quality"]["trust_score"]}
        if report.status == "stale":
            playbook_update["status"] = "stale"
        self.sb.table("playbooks").update(playbook_update).eq("id", selected_row["id"]).execute()
        return True

    def record_outcome(
        self,
        site: str,
        url: str,
        status: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        metadata: dict | None = None,
        tenant_id: str | None = None,
        registry_scope: str = DEFAULT_REGISTRY_SCOPE,
    ) -> bool:
        playbook = self.get_playbook(
            site,
            url,
            task_key=task_key,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        if not playbook:
            return False
        payload = playbook.metadata or {}
        telemetry = payload.get("telemetry", {})
        outcomes = telemetry.get("outcomes", [])
        outcomes.append({
            "timestamp": self._now_iso(),
            "status": status,
            "metadata": metadata or {},
        })
        telemetry["outcomes"] = outcomes[-25:]
        payload["telemetry"] = telemetry
        payload["quality"] = self._compute_quality_summary(
            confidence=playbook.confidence,
            validation=payload.get("validation", {}),
            telemetry=telemetry,
            promotion=payload.get("promotion", {}),
            registry=payload.get("registry", {}),
        )

        route = self._find_route(site, url)
        task_rows = (
            self.sb.table("tasks")
            .select("id")
            .eq("task_key", task_key)
            .limit(1)
            .execute()
            .data
        )
        if not route or not task_rows:
            return False
        playbook_rows = (
            self.sb.table("playbooks")
            .select("id, payload, confidence, variant_key")
            .eq("site_id", route["site_id"])
            .eq("route_id", route["id"])
            .eq("task_id", task_rows[0]["id"])
            .eq("variant_key", playbook.variant_key)
            .eq("status", "active")
            .order("version", desc=True)
            .limit(25)
            .execute()
            .data
        )
        playbook_rows = self._filter_playbooks_by_scope(playbook_rows, tenant_id=tenant_id, registry_scope=registry_scope)
        if not playbook_rows:
            return False
        self.sb.table("playbooks").update({
            "payload": payload,
            "confidence": payload["quality"]["trust_score"],
        }).eq("id", playbook_rows[0]["id"]).execute()
        return True

    def list_active_playbooks_missing_fingerprint(self, limit: int = 100) -> list[dict]:
        try:
            rows = (
                self.sb.table("playbooks")
                .select("id, site_id, route_id, task_id, variant_key, version, payload")
                .eq("status", "active")
                .order("id")
                .limit(limit)
                .execute()
                .data
            )
        except Exception:
            return []
        missing = []
        for row in rows or []:
            payload = row.get("payload") or {}
            if not (payload.get("fingerprint") or {}).get("value"):
                missing.append(row)
        return missing

    def get_playbook_context(self, playbook_id: int) -> dict | None:
        try:
            rows = (
                self.sb.table("playbooks")
                .select("id, site_id, route_id, task_id, variant_key, version, payload, confidence")
                .eq("id", playbook_id)
                .limit(1)
                .execute()
                .data
            )
            if not rows:
                return None
            playbook = rows[0]
            site_rows = (
                self.sb.table("sites")
                .select("domain")
                .eq("id", playbook["site_id"])
                .limit(1)
                .execute()
                .data
            )
            route_rows = (
                self.sb.table("page_routes")
                .select("route_key, example_url")
                .eq("id", playbook["route_id"])
                .limit(1)
                .execute()
                .data
            )
            if not site_rows or not route_rows:
                return None
            return {
                "playbook_id": playbook["id"],
                "site": site_rows[0]["domain"],
                "url": route_rows[0]["example_url"],
                "route_key": route_rows[0]["route_key"],
                "task_id": playbook["task_id"],
                "variant_key": playbook["variant_key"],
                "version": playbook["version"],
                "payload": playbook.get("payload") or {},
                "confidence": playbook.get("confidence"),
            }
        except Exception:
            return None

    def backfill_playbook_fingerprint(
        self,
        playbook_id: int,
        fingerprint: dict,
        source: str = "backfill_validation",
    ) -> bool:
        context = self.get_playbook_context(playbook_id)
        if not context:
            return False
        payload = context["payload"]
        payload["fingerprint"] = fingerprint
        payload["fingerprint_source"] = source
        try:
            self.sb.table("playbooks").update({"payload": payload}).eq("id", playbook_id).execute()
            return True
        except Exception:
            return False

    def _find_route(self, site: str, url: str) -> dict | None:
        site_rows = (
            self.sb.table("sites")
            .select("id")
            .eq("domain", site)
            .limit(1)
            .execute()
            .data
        )
        if not site_rows:
            return None
        site_id = site_rows[0]["id"]
        routes = (
            self.sb.table("page_routes")
            .select("id, route_key, path_pattern")
            .eq("site_id", site_id)
            .execute()
            .data
        )
        matched = self.match_route(url, routes)
        if not matched:
            return None
        matched["site_id"] = site_id
        return matched

    def _get_latest_validation_summary(
        self,
        playbook_payload: dict,
        site_id: int,
        route_id: int,
        task_id: int,
        variant_key: str,
    ) -> dict:
        payload_validation = playbook_payload.get("validation", {})
        try:
            rows = (
                self.sb.table("validation_runs")
                .select(
                    "validated_at, status, success_rate, success_count, failure_count, "
                    "validation_count, schema_version, stored_fingerprint, current_fingerprint, "
                    "fingerprint_match, locator_results"
                )
                .eq("site_id", site_id)
                .eq("route_id", route_id)
                .eq("task_id", task_id)
                .eq("variant_key", variant_key)
                .order("validated_at", desc=True)
                .limit(1)
                .execute()
                .data
            )
            if not rows:
                return payload_validation
            latest = rows[0]
            return {
                "last_validated_at": latest.get("validated_at"),
                "validation_count": latest.get("validation_count", payload_validation.get("validation_count", 0)),
                "success_count": latest.get("success_count", payload_validation.get("success_count", 0)),
                "failure_count": latest.get("failure_count", payload_validation.get("failure_count", 0)),
                "success_rate": latest.get("success_rate"),
                "status": latest.get("status"),
                "schema_version": latest.get("schema_version"),
                "stored_fingerprint": latest.get("stored_fingerprint"),
                "current_fingerprint": latest.get("current_fingerprint"),
                "fingerprint_match": latest.get("fingerprint_match"),
                "locator_results": latest.get("locator_results", []),
                "message": payload_validation.get("message", ""),
            }
        except Exception:
            return payload_validation

    def _insert_validation_history(
        self,
        playbook_id: int,
        site_id: int,
        route_id: int,
        task_id: int,
        variant_key: str,
        report: ValidationReport,
    ) -> bool:
        row = {
            "playbook_id": playbook_id,
            "site_id": site_id,
            "route_id": route_id,
            "task_id": task_id,
            "variant_key": variant_key,
            "validated_at": report.last_validated_at,
            "status": report.status,
            "success_rate": report.success_rate,
            "success_count": report.success_count,
            "failure_count": report.failure_count,
            "validation_count": report.validation_count,
            "schema_version": report.schema_version,
            "stored_fingerprint": report.stored_fingerprint,
            "current_fingerprint": report.current_fingerprint,
            "fingerprint_match": report.fingerprint_match,
            "message": report.message,
            "locator_results": [
                {
                    "element": item.element,
                    "selector_type": item.selector_type,
                    "selector": item.selector,
                    "matched": item.matched,
                    "visible": item.visible,
                    "match_count": item.match_count,
                    "actionable": item.actionable,
                    "ambiguous": item.ambiguous,
                    "error": item.error,
                }
                for item in report.locator_results
            ],
        }
        try:
            self.sb.table("validation_runs").insert(row).execute()
            return True
        except Exception:
            return False

    @staticmethod
    def match_route(url: str, routes: list) -> dict | None:
        try:
            path = urlparse(url).path or "/"
        except Exception:
            path = url
        normalized_path = path if path == "/" else path.rstrip("/") or "/"
        for route in routes:
            try:
                if re.search(route["path_pattern"], path) or re.search(route["path_pattern"], normalized_path):
                    return route
            except Exception:
                continue
        return routes[0] if routes else None

    @staticmethod
    def build_elements(payload: dict) -> dict:
        locators = payload.get("locators", {})
        elements = {}
        for purpose, locs in locators.items():
            if not locs:
                continue
            best = sorted(locs, key=lambda item: item.get("priority", 99))[0]
            normalized = AtlasRegistry._normalize_selector_record({
                "type": best.get("type"),
                "selector": best.get("value"),
                "confidence": best.get("confidence", 0.5),
            })
            if not normalized:
                continue
            elements[purpose] = {
                "type": normalized.get("type"),
                "selector": normalized.get("selector"),
                "confidence": normalized.get("confidence", 0.5),
            }
        return elements

    @staticmethod
    def _now_iso() -> str:
        return now_iso()

    @staticmethod
    def _fingerprint_value(payload: dict | None) -> str | None:
        if not payload:
            return None
        return (payload.get("fingerprint") or {}).get("value")

    def _normalize_element_locator(self, info: dict) -> dict | None:
        return self._normalize_selector_record({
            "type": info.get("type"),
            "selector": info.get("selector"),
            "confidence": info.get("confidence", 0.0),
        })

    @staticmethod
    def _locator_priority(locator: dict) -> int:
        selector_type = (locator.get("type") or "").strip().lower()
        selector = (locator.get("selector") or "").strip()
        if selector_type == "data_testid":
            return 1
        if selector_type == "aria_label":
            return 2
        if selector_type == "role":
            return 3
        if selector_type == "css":
            if selector.startswith("input[") or selector.startswith("button["):
                return 4
            return 5
        if selector_type == "text":
            return 7
        return 9
