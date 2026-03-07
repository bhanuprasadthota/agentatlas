import os
import secrets
from pathlib import Path
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentatlas.atlas import Atlas
from agentatlas.ui.admin import render_admin_html


class ResolveSchemaRequest(BaseModel):
    site: str
    url: str
    task_key: str = "generic_extract"
    variant_key: str | None = None
    registry_scope: str = "auto"


class ResolveLocatorRequest(ResolveSchemaRequest):
    element_name: str


class ValidateRequest(ResolveSchemaRequest):
    learn_if_missing: bool = True
    persist: bool = True
    headless: bool = True
    relearn_on_degraded: bool = True


class OutcomeRequest(ResolveSchemaRequest):
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromotePlaybookRequest(BaseModel):
    playbook_id: str
    reviewer: str
    approved: bool = True
    notes: str = ""


class ScopeDiffRequest(BaseModel):
    site: str
    url: str
    task_key: str = "generic_extract"
    variant_key: str | None = None


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
tenant_header = APIKeyHeader(name="X-Tenant-ID", auto_error=False)


def get_api_keys() -> set[str]:
    keys: set[str] = set()
    single_key = os.getenv("AGENTATLAS_API_KEY", "").strip()
    if single_key:
        keys.add(single_key)
    multi_keys = os.getenv("AGENTATLAS_API_KEYS", "")
    for value in multi_keys.split(","):
        candidate = value.strip()
        if candidate:
            keys.add(candidate)
    return keys


def get_tenant_api_keys() -> dict[str, set[str]]:
    raw = os.getenv("AGENTATLAS_TENANT_API_KEYS", "").strip()
    tenant_keys: dict[str, set[str]] = {}
    if not raw:
        return tenant_keys
    for tenant_entry in raw.split(";"):
        if not tenant_entry.strip():
            continue
        tenant_id, separator, keys = tenant_entry.partition(":")
        if not separator:
            continue
        normalized_tenant_id = tenant_id.strip()
        parsed_keys = {item.strip() for item in keys.split("|") if item.strip()}
        if normalized_tenant_id and parsed_keys:
            tenant_keys[normalized_tenant_id] = parsed_keys
    return tenant_keys


def require_api_key(
    api_key: str | None = Depends(api_key_header),
    tenant_id: str | None = Depends(tenant_header),
) -> dict[str, str | None]:
    tenant_api_keys = get_tenant_api_keys()
    if tenant_api_keys:
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Missing tenant id.")
        configured_tenant_keys = tenant_api_keys.get(tenant_id)
        if not configured_tenant_keys:
            raise HTTPException(status_code=401, detail="Unknown tenant.")
        if api_key and any(secrets.compare_digest(api_key, configured) for configured in configured_tenant_keys):
            return {"tenant_id": tenant_id, "api_key": api_key}
        raise HTTPException(status_code=401, detail="Invalid or missing tenant API key.")

    configured_keys = get_api_keys()
    if not configured_keys:
        return {"tenant_id": tenant_id, "api_key": api_key}
    if api_key and any(secrets.compare_digest(api_key, configured) for configured in configured_keys):
        return {"tenant_id": tenant_id, "api_key": api_key}
    raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def get_atlas() -> Atlas:
    return Atlas(use_api=False)


def serialize_dataclass(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


def create_app() -> FastAPI:
    app = FastAPI(
        title="AgentAtlas API",
        version="0.1.0",
        description="Hosted API for shared web interaction memory with validation.",
    )
    static_dir = Path(__file__).with_name("ui") / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/admin", response_class=HTMLResponse)
    async def admin() -> str:
        return render_admin_html()

    @app.post("/v1/schema/resolve")
    async def resolve_schema(
        request: ResolveSchemaRequest,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        schema = await atlas.get_schema(
            site=request.site,
            url=request.url,
            variant_key=request.variant_key,
            tenant_id=auth.get("tenant_id"),
            registry_scope=request.registry_scope,
        )
        playbook = await atlas.get_playbook(
            site=request.site,
            url=request.url,
            task_key=request.task_key,
            variant_key=request.variant_key,
            tenant_id=auth.get("tenant_id"),
            registry_scope=request.registry_scope,
        )
        return {
            "schema": serialize_dataclass(schema),
            "playbook": serialize_dataclass(playbook) if playbook else None,
        }

    @app.post("/v1/locator/resolve")
    async def resolve_locator(
        request: ResolveLocatorRequest,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        locator = await atlas.resolve_locator(
            site=request.site,
            url=request.url,
            element_name=request.element_name,
            variant_key=request.variant_key,
            tenant_id=auth.get("tenant_id"),
            registry_scope=request.registry_scope,
        )
        if not locator:
            raise HTTPException(status_code=404, detail="Locator not found for requested element.")
        playbook = await atlas.get_playbook(
            site=request.site,
            url=request.url,
            task_key=request.task_key,
            variant_key=request.variant_key,
            tenant_id=auth.get("tenant_id"),
            registry_scope=request.registry_scope,
        )
        return {
            "element_name": request.element_name,
            "locator": locator,
            "playbook": serialize_dataclass(playbook) if playbook else None,
        }

    @app.post("/v1/validate")
    async def validate(
        request: ValidateRequest,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        report = await atlas.validate(
            site=request.site,
            url=request.url,
            task_key=request.task_key,
            variant_key=request.variant_key,
            tenant_id=auth.get("tenant_id"),
            registry_scope=request.registry_scope,
            learn_if_missing=request.learn_if_missing,
            persist=request.persist,
            headless=request.headless,
            relearn_on_degraded=request.relearn_on_degraded,
        )
        return {"report": serialize_dataclass(report)}

    @app.post("/v1/outcome")
    async def record_outcome(
        request: OutcomeRequest,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        recorded = await atlas.record_outcome(
            site=request.site,
            url=request.url,
            status=request.status,
            task_key=request.task_key,
            variant_key=request.variant_key,
            tenant_id=auth.get("tenant_id"),
            registry_scope=request.registry_scope,
            metadata=request.metadata,
        )
        return {"recorded": recorded}

    @app.get("/v1/benchmarks/runs", dependencies=[Depends(require_api_key)])
    async def list_benchmark_runs(
        suite_name: str = "warm_start_reliability",
        limit: int = 20,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        runs = atlas.registry.list_benchmark_runs(
            suite_name=suite_name,
            limit=limit,
            tenant_id=auth.get("tenant_id"),
        )
        return {"runs": runs}

    @app.get("/v1/benchmarks/compare")
    async def compare_benchmark_runs(
        suite_name: str = "warm_start_reliability",
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        runs = atlas.registry.list_benchmark_runs(
            suite_name=suite_name,
            limit=2,
            tenant_id=auth.get("tenant_id"),
        )
        if len(runs) < 2:
            return {
                "suite_name": suite_name,
                "status": "insufficient_data",
                "message": "Need at least two benchmark runs to compare.",
            }

        latest, previous = runs[0], runs[1]
        previous_by_name = {item["name"]: item for item in previous.get("payload", [])}
        regressions = []

        for item in latest.get("payload", []):
            prior = previous_by_name.get(item["name"])
            if not prior:
                continue
            if item.get("validation_status") != "healthy" and prior.get("validation_status") == "healthy":
                regressions.append({
                    "workflow": item["name"],
                    "kind": "validation_status",
                    "previous": prior.get("validation_status"),
                    "current": item.get("validation_status"),
                })
            if bool(item.get("warm_registry_hit")) is False and bool(prior.get("warm_registry_hit")) is True:
                regressions.append({
                    "workflow": item["name"],
                    "kind": "warm_registry_hit",
                    "previous": prior.get("warm_registry_hit"),
                    "current": item.get("warm_registry_hit"),
                })
            prior_failed = len(prior.get("failed_locators") or [])
            current_failed = len(item.get("failed_locators") or [])
            if current_failed > prior_failed:
                regressions.append({
                    "workflow": item["name"],
                    "kind": "failed_locator_count",
                    "previous": prior_failed,
                    "current": current_failed,
                })

        return {
            "suite_name": suite_name,
            "latest_run_at": latest.get("run_at"),
            "previous_run_at": previous.get("run_at"),
            "latest_warm_hit_rate": latest.get("warm_hit_rate"),
            "previous_warm_hit_rate": previous.get("warm_hit_rate"),
            "latest_healthy_count": latest.get("healthy_count"),
            "previous_healthy_count": previous.get("healthy_count"),
            "regressions": regressions,
            "status": "regression" if regressions else "stable",
        }

    @app.get("/v1/benchmarks/dashboard")
    async def benchmark_dashboard(
        suite_name: str = "warm_start_reliability",
        limit: int = 10,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        return atlas.registry.get_benchmark_dashboard(
            suite_name=suite_name,
            tenant_id=auth.get("tenant_id"),
            limit=limit,
        )

    @app.get("/v1/review/queue")
    async def review_queue(
        limit: int = 50,
        registry_scope: str = "public",
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        queue = atlas.registry.list_review_queue(
            tenant_id=auth.get("tenant_id"),
            registry_scope=registry_scope,
            limit=limit,
        )
        return {"queue": queue}

    @app.get("/v1/review/audit")
    async def review_audit(
        limit: int = 100,
        registry_scope: str = "auto",
        playbook_id: str | None = None,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        audit = atlas.registry.list_review_audit(
            tenant_id=auth.get("tenant_id"),
            registry_scope=registry_scope,
            playbook_id=playbook_id,
            limit=limit,
        )
        return {"audit": audit}

    @app.post("/v1/review/promote")
    async def promote_playbook(
        request: PromotePlaybookRequest,
        atlas: Atlas = Depends(get_atlas),
        _auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        promoted = atlas.registry.promote_playbook(
            playbook_id=request.playbook_id,
            reviewer=request.reviewer,
            approved=request.approved,
            notes=request.notes,
        )
        return {"promoted": promoted}

    @app.post("/v1/review/diff")
    async def review_diff(
        request: ScopeDiffRequest,
        atlas: Atlas = Depends(get_atlas),
        auth: dict[str, str | None] = Depends(require_api_key),
    ) -> dict[str, Any]:
        variant_key = request.variant_key or atlas.infer_variant_key(request.url)
        diff = atlas.registry.get_route_scope_diff(
            site=request.site,
            url=request.url,
            task_key=request.task_key,
            variant_key=variant_key,
            tenant_id=auth.get("tenant_id"),
        )
        if not diff:
            raise HTTPException(status_code=404, detail="No route scope diff found.")
        return diff

    return app


app = create_app()
