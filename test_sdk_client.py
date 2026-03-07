import json

from agentatlas import Atlas


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_get_schema_uses_hosted_api(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["api_key"] = request.headers.get("X-api-key")
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "schema": {
                    "site": "example.com",
                    "url": "https://example.com",
                    "route_key": "home",
                    "status": "found",
                    "confidence": 0.95,
                    "elements": {"hero": {"type": "role", "selector": "heading+Example", "confidence": 0.9}},
                    "source": "registry",
                    "tokens_used": 0,
                    "message": "Schema found in registry. No LLM used.",
                },
                "playbook": None,
            }
        )

    monkeypatch.setattr("agentatlas.client.urlopen", fake_urlopen)

    atlas = Atlas(api_url="https://api.agentatlas.dev", api_key="sdk-secret", use_api=True, api_timeout=9.5)
    schema = __import__("asyncio").run(
        atlas.get_schema(site="example.com", url="https://example.com", max_learn_seconds=12.5)
    )

    assert captured["url"] == "https://api.agentatlas.dev/v1/schema/resolve"
    assert captured["body"] == {
        "site": "example.com",
        "url": "https://example.com",
        "task_key": "generic_extract",
        "variant_key": "desktop_enUS_loggedout",
        "registry_scope": "auto",
        "max_learn_seconds": 12.5,
    }
    assert captured["api_key"] == "sdk-secret"
    assert captured["timeout"] == 9.5
    assert schema.route_key == "home"
    assert schema.source == "registry"


def test_hosted_sdk_methods(monkeypatch):
    responses = {
        "https://api.agentatlas.dev/v1/locator/resolve": {
            "element_name": "hero",
            "locator": {"type": "role", "selector": "heading+Example", "confidence": 0.9},
            "playbook": None,
        },
        "https://api.agentatlas.dev/v1/validate": {
            "report": {
                "site": "example.com",
                "url": "https://example.com",
                "route_key": "home",
                "status": "healthy",
                "source": "registry",
                "validation_count": 2,
                "success_count": 1,
                "failure_count": 0,
                "success_rate": 1.0,
                "last_validated_at": "2026-03-07T09:00:00+00:00",
                "schema_version": 1,
                "stored_fingerprint": "abc123",
                "current_fingerprint": "abc123",
                "fingerprint_match": True,
                "locator_results": [
                    {
                        "element": "hero",
                        "selector_type": "role",
                        "selector": "heading+Example",
                        "matched": True,
                        "visible": True,
                        "match_count": 1,
                        "actionable": True,
                        "ambiguous": False,
                        "error": "",
                    }
                ],
                "message": "Validated 1 locators.",
            }
        },
        "https://api.agentatlas.dev/v1/outcome": {"recorded": True},
        "https://api.agentatlas.dev/v1/review/queue?limit=50&registry_scope=public": {
            "queue": [
                {
                    "playbook_id": "pb-1",
                    "site": "github.com",
                    "url": "https://github.com/login",
                    "route_key": "login",
                    "variant_key": "desktop_enUS_loggedout",
                    "confidence": 0.6,
                    "review_status": "review_required",
                    "review_reason": "domain_class:social_auth",
                    "registry_scope": "public",
                    "tenant_id": "tenant-a",
                    "pending_age_hours": 30.0,
                    "overdue": True,
                    "flag_count": 1,
                }
            ]
        },
        "https://api.agentatlas.dev/v1/review/dashboard?limit=100&registry_scope=public": {
            "queue_size": 1,
            "overdue_count": 1,
            "oldest_pending_hours": 30.0,
            "sla_hours": 24,
            "flagged_count": 1,
            "reasons": {"domain_class:social_auth": 1},
        },
        "https://api.agentatlas.dev/v1/review/audit?limit=100&registry_scope=auto": {
            "audit": [
                {
                    "playbook_id": "pb-1",
                    "timestamp": "2026-03-07T10:00:00+00:00",
                    "reviewer": "qa@example.com",
                    "reviewer_role": "admin",
                    "action": "approved",
                    "notes": "looks good",
                    "site": "github.com",
                    "url": "https://github.com/login",
                    "route_key": "login",
                    "variant_key": "desktop_enUS_loggedout",
                    "tenant_id": "tenant-a",
                    "registry_scope": "public",
                }
            ]
        },
        "https://api.agentatlas.dev/v1/review/promote": {"promoted": True},
        "https://api.agentatlas.dev/v1/review/flag": {"flagged": True},
        "https://api.agentatlas.dev/v1/review/diff": {
            "site": "github.com",
            "url": "https://github.com/login",
            "task_key": "generic_extract",
            "variant_key": "desktop_enUS_loggedout",
            "tenant_id": "tenant-a",
            "decision": {"winner": "public", "reason": "public_memory_stronger_than_private_on_fingerprint_conflict"},
            "private": {"scope": "private"},
            "public": {"scope": "public"},
            "route_differences": [{"element": "login_button"}],
        },
    }
    captured = []

    def fake_urlopen(request, timeout=0):
        captured.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")) if request.data else None,
                "api_key": request.headers.get("X-api-key"),
                "tenant_id": request.headers.get("X-tenant-id"),
                "timeout": timeout,
            }
        )
        return _FakeResponse(responses[request.full_url])

    monkeypatch.setattr("agentatlas.client.urlopen", fake_urlopen)

    atlas = Atlas(
        api_url="https://api.agentatlas.dev",
        api_key="sdk-secret",
        tenant_id="tenant-a",
        use_api=True,
        api_timeout=11.0,
    )

    locator = __import__("asyncio").run(
        atlas.resolve_locator(site="example.com", url="https://example.com", element_name="hero")
    )
    report = __import__("asyncio").run(atlas.validate(site="example.com", url="https://example.com"))
    recorded = __import__("asyncio").run(
        atlas.record_outcome(site="example.com", url="https://example.com", status="success")
    )
    queue = __import__("asyncio").run(atlas.list_review_queue())
    review_dashboard = __import__("asyncio").run(atlas.get_review_dashboard())
    audit = __import__("asyncio").run(atlas.list_review_audit())
    promoted = __import__("asyncio").run(atlas.promote_playbook(playbook_id="pb-1", reviewer="qa@example.com"))
    flagged = __import__("asyncio").run(
        atlas.flag_schema(site="github.com", url="https://github.com/login", reporter="qa@example.com", reason="bad_selector")
    )
    diff = __import__("asyncio").run(atlas.get_route_scope_diff(site="github.com", url="https://github.com/login"))

    assert locator["selector"] == "heading+Example"
    assert report.status == "healthy"
    assert report.locator_results[0].actionable is True
    assert recorded is True
    assert queue[0]["playbook_id"] == "pb-1"
    assert review_dashboard["overdue_count"] == 1
    assert audit[0]["reviewer"] == "qa@example.com"
    assert promoted is True
    assert flagged is True
    assert diff["decision"]["winner"] == "public"
    assert [item["url"] for item in captured] == [
        "https://api.agentatlas.dev/v1/locator/resolve",
        "https://api.agentatlas.dev/v1/validate",
        "https://api.agentatlas.dev/v1/outcome",
        "https://api.agentatlas.dev/v1/review/queue?limit=50&registry_scope=public",
        "https://api.agentatlas.dev/v1/review/dashboard?limit=100&registry_scope=public",
        "https://api.agentatlas.dev/v1/review/audit?limit=100&registry_scope=auto",
        "https://api.agentatlas.dev/v1/review/promote",
        "https://api.agentatlas.dev/v1/review/flag",
        "https://api.agentatlas.dev/v1/review/diff",
    ]
    assert captured[0]["body"]["variant_key"] == "desktop_enUS_loggedout"
    assert captured[1]["body"]["variant_key"] == "desktop_enUS_loggedout"
    assert captured[2]["body"]["variant_key"] == "desktop_enUS_loggedout"
    assert captured[3]["body"] is None
    assert captured[4]["body"] is None
    assert all(item["api_key"] == "sdk-secret" for item in captured)
    assert all(item["tenant_id"] == "tenant-a" for item in captured)
    assert all(item["timeout"] == 11.0 for item in captured)
