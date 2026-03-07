import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agentatlas.models import (
    LocatorResolution,
    PlaybookRecord,
    ResolveLocatorResponse,
    ResolveSchemaResponse,
    ReviewAuditEvent,
    ReviewQueueItem,
    RouteScopeDiff,
    SiteSchema,
    ValidationReport,
)


class AtlasHostedClientMixin:
    def _require_direct_mode(self, method_name: str) -> None:
        if self.use_api:
            raise RuntimeError(
                f"{method_name} is not available in hosted client mode yet. "
                "Use a direct Atlas instance or call the hosted API endpoint explicitly."
            )

    def _get_schema_via_api(self, site: str, url: str, variant_key: str, registry_scope: str) -> SiteSchema:
        return self._resolve_schema_via_api(
            site=site,
            url=url,
            variant_key=variant_key,
            registry_scope=registry_scope,
        ).schema

    def _resolve_schema_via_api(
        self,
        site: str,
        url: str,
        task_key: str = "generic_extract",
        variant_key: str = "desktop_enUS_loggedout",
        registry_scope: str = "auto",
    ) -> ResolveSchemaResponse:
        body = self._request_api_json(
            method="POST",
            path="/v1/schema/resolve",
            payload={
                "site": site,
                "url": url,
                "task_key": task_key,
                "variant_key": variant_key,
                "registry_scope": registry_scope,
            },
        )
        schema_payload = body.get("schema")
        if not schema_payload:
            raise RuntimeError("AgentAtlas API schema resolve response did not include a schema payload.")
        playbook_payload = body.get("playbook")
        return ResolveSchemaResponse(
            schema=SiteSchema(**schema_payload),
            playbook=PlaybookRecord(**playbook_payload) if playbook_payload else None,
        )

    def _resolve_locator_via_api(
        self,
        site: str,
        url: str,
        element_name: str,
        variant_key: str,
        registry_scope: str,
    ) -> ResolveLocatorResponse:
        body = self._request_api_json(
            method="POST",
            path="/v1/locator/resolve",
            payload={
                "site": site,
                "url": url,
                "element_name": element_name,
                "variant_key": variant_key,
                "registry_scope": registry_scope,
            },
        )
        playbook_payload = body.get("playbook")
        return ResolveLocatorResponse(
            element_name=body.get("element_name", element_name),
            locator=body.get("locator"),
            playbook=PlaybookRecord(**playbook_payload) if playbook_payload else None,
        )

    def _validate_via_api(
        self,
        site: str,
        url: str,
        task_key: str,
        variant_key: str,
        registry_scope: str,
        learn_if_missing: bool,
        persist: bool,
        headless: bool,
        relearn_on_degraded: bool,
    ) -> ValidationReport:
        body = self._request_api_json(
            method="POST",
            path="/v1/validate",
            payload={
                "site": site,
                "url": url,
                "task_key": task_key,
                "variant_key": variant_key,
                "registry_scope": registry_scope,
                "learn_if_missing": learn_if_missing,
                "persist": persist,
                "headless": headless,
                "relearn_on_degraded": relearn_on_degraded,
            },
        )
        report = body.get("report")
        if not report:
            raise RuntimeError("AgentAtlas API validate response did not include a report payload.")
        report["locator_results"] = [
            LocatorResolution(**item) for item in report.get("locator_results", [])
        ]
        return ValidationReport(**report)

    def _record_outcome_via_api(
        self,
        site: str,
        url: str,
        status: str,
        task_key: str,
        variant_key: str,
        registry_scope: str,
        metadata: dict | None,
    ) -> bool:
        body = self._request_api_json(
            method="POST",
            path="/v1/outcome",
            payload={
                "site": site,
                "url": url,
                "status": status,
                "task_key": task_key,
                "variant_key": variant_key,
                "registry_scope": registry_scope,
                "metadata": metadata or {},
            },
        )
        return bool(body.get("recorded"))

    def _list_review_queue_via_api(self, limit: int, registry_scope: str) -> list[ReviewQueueItem]:
        body = self._request_api_json(
            method="GET",
            path=f"/v1/review/queue?limit={limit}&registry_scope={registry_scope}",
        )
        return [ReviewQueueItem(**item) for item in body.get("queue", [])]

    def _list_review_audit_via_api(self, limit: int, registry_scope: str) -> list[ReviewAuditEvent]:
        body = self._request_api_json(
            method="GET",
            path=f"/v1/review/audit?limit={limit}&registry_scope={registry_scope}",
        )
        return [ReviewAuditEvent(**item) for item in body.get("audit", [])]

    def _promote_playbook_via_api(self, playbook_id, reviewer: str, approved: bool, notes: str) -> bool:
        body = self._request_api_json(
            method="POST",
            path="/v1/review/promote",
            payload={
                "playbook_id": str(playbook_id),
                "reviewer": reviewer,
                "approved": approved,
                "notes": notes,
            },
        )
        return bool(body.get("promoted"))

    def _get_route_scope_diff_via_api(
        self,
        site: str,
        url: str,
        task_key: str,
        variant_key: str,
    ) -> RouteScopeDiff:
        body = self._request_api_json(
            method="POST",
            path="/v1/review/diff",
            payload={
                "site": site,
                "url": url,
                "task_key": task_key,
                "variant_key": variant_key,
            },
        )
        return RouteScopeDiff(**body)

    def _request_api_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.tenant_id:
            headers["X-Tenant-ID"] = self.tenant_id
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url=f"{self.api_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        attempts = int(os.getenv("AGENTATLAS_API_RETRIES", "3"))
        backoff_seconds = float(os.getenv("AGENTATLAS_API_BACKOFF_SECONDS", "0.5"))
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                with urlopen(request, timeout=self.api_timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if 500 <= exc.code < 600 and attempt < attempts:
                    self.logger.warning(
                        "agentatlas_api_retry",
                        extra={"path": path, "attempt": attempt, "status_code": exc.code},
                    )
                    time.sleep(backoff_seconds * attempt)
                    last_error = RuntimeError(f"AgentAtlas API request failed with {exc.code}: {detail}")
                    continue
                raise RuntimeError(f"AgentAtlas API request failed with {exc.code}: {detail}") from exc
            except URLError as exc:
                last_error = RuntimeError(f"AgentAtlas API request failed: {exc.reason}")
                if attempt < attempts:
                    self.logger.warning(
                        "agentatlas_api_retry",
                        extra={"path": path, "attempt": attempt, "reason": str(exc.reason)},
                    )
                    time.sleep(backoff_seconds * attempt)
                    continue
                raise last_error from exc
        if last_error:
            raise last_error
        raise RuntimeError("AgentAtlas API request failed unexpectedly.")

    def infer_variant_key(self, url: str, variant_key: str | None = None) -> str:
        if variant_key:
            return variant_key
        device_class = (os.getenv("AGENTATLAS_DEVICE_CLASS") or "desktop").strip().lower()
        locale = (os.getenv("AGENTATLAS_LOCALE") or "en-US").strip()
        auth_state = (os.getenv("AGENTATLAS_AUTH_STATE") or "").strip().lower()
        region = (os.getenv("AGENTATLAS_REGION") or "").strip().lower()
        if not auth_state:
            lowered_url = (url or "").lower()
            auth_state = "loggedout" if any(token in lowered_url for token in ("/login", "/signin", "/auth")) else "loggedout"
        normalized_locale = re.sub(r"[^A-Za-z]", "", locale)
        if len(normalized_locale) >= 4:
            normalized_locale = normalized_locale[:2].lower() + normalized_locale[2:].upper()
        else:
            normalized_locale = "enUS"
        parts = [device_class, normalized_locale, auth_state]
        if region:
            parts.append(region)
        return "_".join(parts)
