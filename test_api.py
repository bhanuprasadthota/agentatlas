import os
import pytest


fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from agentatlas.api import create_app, get_atlas


@dataclass
class _FakeSchema:
    site: str = "example.com"
    url: str = "https://example.com"
    route_key: str = "home"
    status: str = "found"
    confidence: float = 0.9
    elements: dict = field(default_factory=lambda: {"hero": {"type": "role", "selector": "heading+Example", "confidence": 0.9}})
    source: str = "registry"
    tokens_used: int = 0
    message: str = "ok"


@dataclass
class _FakePlaybook:
    site: str = "example.com"
    url: str = "https://example.com"
    route_key: str = "home"
    task_key: str = "generic_extract"
    variant_key: str = "desktop_enUS_loggedout"
    confidence: float = 0.9
    elements: dict = field(default_factory=dict)
    source: str = "registry"
    schema_version: int = 2
    fingerprint: str | None = "abc123"
    last_validated_at: str | None = "2026-03-07T09:00:00+00:00"
    success_rate: float | None = 1.0
    validation_count: int = 4
    metadata: dict = field(default_factory=dict)


@dataclass
class _FakeLocatorResolution:
    element: str = "hero"
    selector_type: str = "role"
    selector: str = "heading+Example"
    matched: bool = True
    visible: bool = True
    match_count: int = 1
    actionable: bool = True
    ambiguous: bool = False
    error: str = ""


@dataclass
class _FakeValidationReport:
    site: str = "example.com"
    url: str = "https://example.com"
    route_key: str = "home"
    status: str = "healthy"
    source: str = "registry"
    validation_count: int = 4
    success_count: int = 1
    failure_count: int = 0
    success_rate: float = 1.0
    last_validated_at: str = "2026-03-07T09:00:00+00:00"
    schema_version: int | None = 2
    stored_fingerprint: str | None = "abc123"
    current_fingerprint: str | None = "abc123"
    fingerprint_match: bool | None = True
    locator_results: list = field(default_factory=lambda: [_FakeLocatorResolution()])
    message: str = "Validated 1 locators."


class _FakeRegistry:
    def list_benchmark_runs(self, suite_name: str, limit: int = 2, tenant_id=None):
        return [
            {
                "run_at": "2026-03-07T09:01:27.752199+00:00",
                "warm_hit_rate": 1,
                "healthy_count": 2,
                "payload": [
                    {"name": "one", "warm_registry_hit": True, "validation_status": "healthy", "failed_locators": []},
                    {"name": "two", "warm_registry_hit": True, "validation_status": "healthy", "failed_locators": []},
                ],
            },
            {
                "run_at": "2026-03-07T08:58:46.142231+00:00",
                "warm_hit_rate": 1,
                "healthy_count": 2,
                "payload": [
                    {"name": "one", "warm_registry_hit": True, "validation_status": "healthy", "failed_locators": []},
                    {"name": "two", "warm_registry_hit": True, "validation_status": "healthy", "failed_locators": []},
                ],
            },
        ][:limit]

    def get_benchmark_dashboard(self, suite_name: str, tenant_id=None, limit: int = 10):
        return {
            "suite_name": suite_name,
            "tenant_id": tenant_id,
            "run_count": 2,
            "latest_run_at": "2026-03-07T09:01:27.752199+00:00",
            "latest_status": "healthy",
            "warm_hit_rate_trend": [1, 1],
            "healthy_count_trend": [2, 2],
            "categories": {
                "auth_wall": {
                    "workflow_count": 2,
                    "healthy_count": 2,
                    "degraded_count": 0,
                    "failed_count": 0,
                    "warm_hits": 2,
                    "warm_hit_rate": 1.0,
                }
            },
        }

    def list_review_queue(self, tenant_id=None, registry_scope="public", limit: int = 50):
        return [
            {
                "playbook_id": "pb-1",
                "site": "github.com",
                "url": "https://github.com/login",
                "route_key": "login",
                "variant_key": "desktop_enUS_loggedout",
                "confidence": 0.6,
                "review_status": "review_required",
                "review_reason": "domain_class:social_auth",
                "registry_scope": registry_scope,
                "tenant_id": tenant_id,
            }
        ][:limit]

    def list_review_audit(self, tenant_id=None, registry_scope="auto", playbook_id=None, limit: int = 100):
        return [
            {
                "playbook_id": playbook_id or "pb-1",
                "site": "github.com",
                "url": "https://github.com/login",
                "route_key": "login",
                "variant_key": "desktop_enUS_loggedout",
                "tenant_id": tenant_id,
                "registry_scope": registry_scope,
                "timestamp": "2026-03-07T10:00:00+00:00",
                "reviewer": "qa@example.com",
                "reviewer_role": "admin",
                "action": "approved",
                "notes": "Looks good",
            }
        ][:limit]

    def promote_playbook(self, playbook_id, reviewer: str, approved: bool = True, notes: str = ""):
        return playbook_id == "pb-1" and reviewer == "qa@example.com"

    def get_route_scope_diff(self, site: str, url: str, task_key: str = "generic_extract", variant_key: str = "desktop_enUS_loggedout", tenant_id=None):
        return {
            "site": site,
            "url": url,
            "task_key": task_key,
            "variant_key": variant_key,
            "tenant_id": tenant_id,
            "decision": {"winner": "public", "reason": "public_memory_stronger_than_private_on_fingerprint_conflict"},
            "private": {"scope": "private", "trust_score": 0.55},
            "public": {"scope": "public", "trust_score": 0.91},
            "route_differences": [
                {"element": "login_button", "private": {"selector": "button+Sign in"}, "public": {"selector": "button+Sign in to GitHub"}}
            ],
        }


class _FakeAtlas:
    def __init__(self):
        self.registry = _FakeRegistry()

    def infer_variant_key(self, url: str, variant_key: str | None = None):
        return variant_key or "desktop_enUS_loggedout"

    async def get_schema(self, site: str, url: str, **_kwargs):
        return _FakeSchema(site=site, url=url)

    async def get_playbook(self, site: str, url: str, task_key: str = "generic_extract", variant_key: str = "desktop_enUS_loggedout", **_kwargs):
        return _FakePlaybook(site=site, url=url, task_key=task_key, variant_key=variant_key)

    async def resolve_locator(self, site: str, url: str, element_name: str, **_kwargs):
        if element_name == "missing":
            return None
        return {"type": "role", "selector": "heading+Example", "confidence": 0.9}

    async def validate(self, **_kwargs):
        return _FakeValidationReport()

    async def record_outcome(self, **_kwargs):
        return True


def test_api_endpoints():
    app = create_app()
    app.dependency_overrides[get_atlas] = lambda: _FakeAtlas()
    client = TestClient(app)

    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/v1/schema/resolve", json={"site": "example.com", "url": "https://example.com"}).json()["schema"]["route_key"] == "home"
    assert client.post("/v1/locator/resolve", json={"site": "example.com", "url": "https://example.com", "element_name": "hero"}).json()["locator"]["selector"] == "heading+Example"
    assert client.post("/v1/locator/resolve", json={"site": "example.com", "url": "https://example.com", "element_name": "missing"}).status_code == 404
    assert client.post("/v1/validate", json={"site": "example.com", "url": "https://example.com"}).json()["report"]["status"] == "healthy"
    assert client.post("/v1/outcome", json={"site": "example.com", "url": "https://example.com", "status": "success"}).json()["recorded"] is True
    assert len(client.get("/v1/benchmarks/runs").json()["runs"]) == 2
    assert client.get("/v1/benchmarks/compare").json()["status"] == "stable"
    assert client.get("/v1/benchmarks/dashboard").json()["categories"]["auth_wall"]["healthy_count"] == 2
    assert client.get("/v1/review/queue").json()["queue"][0]["review_status"] == "review_required"
    assert client.get("/admin").status_code == 200
    assert client.get("/static/admin.css").status_code == 200
    assert client.get("/static/admin.js").status_code == 200
    assert client.get("/v1/review/audit").json()["audit"][0]["reviewer"] == "qa@example.com"
    assert client.post("/v1/review/promote", json={"playbook_id": "pb-1", "reviewer": "qa@example.com"}).json()["promoted"] is True
    diff = client.post("/v1/review/diff", json={"site": "github.com", "url": "https://github.com/login"})
    assert diff.status_code == 200
    assert diff.json()["decision"]["winner"] == "public"


def test_api_key_auth(monkeypatch):
    monkeypatch.setenv("AGENTATLAS_API_KEY", "secret-key")
    app = create_app()
    app.dependency_overrides[get_atlas] = lambda: _FakeAtlas()
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.post("/v1/schema/resolve", json={"site": "example.com", "url": "https://example.com"}).status_code == 401

    authorized = client.post(
        "/v1/schema/resolve",
        json={"site": "example.com", "url": "https://example.com"},
        headers={"X-API-Key": "secret-key"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["schema"]["route_key"] == "home"
    monkeypatch.delenv("AGENTATLAS_API_KEY", raising=False)


def test_tenant_api_key_auth(monkeypatch):
    monkeypatch.delenv("AGENTATLAS_API_KEY", raising=False)
    monkeypatch.delenv("AGENTATLAS_API_KEYS", raising=False)
    monkeypatch.setenv("AGENTATLAS_TENANT_API_KEYS", "tenant-a:key-a|key-a-2;tenant-b:key-b")
    app = create_app()
    app.dependency_overrides[get_atlas] = lambda: _FakeAtlas()
    client = TestClient(app)

    missing_tenant = client.post(
        "/v1/schema/resolve",
        json={"site": "example.com", "url": "https://example.com"},
        headers={"X-API-Key": "key-a"},
    )
    assert missing_tenant.status_code == 401

    wrong_tenant = client.post(
        "/v1/schema/resolve",
        json={"site": "example.com", "url": "https://example.com"},
        headers={"X-API-Key": "key-a", "X-Tenant-ID": "tenant-b"},
    )
    assert wrong_tenant.status_code == 401

    authorized = client.post(
        "/v1/schema/resolve",
        json={"site": "example.com", "url": "https://example.com"},
        headers={"X-API-Key": "key-a", "X-Tenant-ID": "tenant-a"},
    )
    assert authorized.status_code == 200
    monkeypatch.delenv("AGENTATLAS_TENANT_API_KEYS", raising=False)
