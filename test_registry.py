import unittest

from agentatlas.atlas import Atlas
from agentatlas.executor import AgentExecutor
from agentatlas.registry import AtlasRegistry, build_route_fingerprint, path_pattern_from_signature, path_signature
from agentatlas.models import LocatorResolution, ValidationReport
from test_execute import BenchmarkResult


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _FakeResult(self._data)


class _FakeSupabase:
    def __init__(self, validation_rows=None):
        self.validation_rows = validation_rows or []

    def table(self, name):
        if name == "validation_runs":
            return _FakeQuery(self.validation_rows)
        raise AssertionError(f"Unexpected table requested: {name}")


class _SequentialQuery:
    def __init__(self, provider):
        self.provider = provider

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        value = self.provider()
        if isinstance(value, Exception):
            raise value
        return _FakeResult(value)


class _ReadFallbackSupabase:
    def __init__(self):
        self.fail_playbooks = False

    def table(self, name):
        if name == "sites":
            return _SequentialQuery(lambda: [{"id": 1}])
        if name == "page_routes":
            return _SequentialQuery(lambda: [{"id": 2, "route_key": "home", "path_pattern": r"^/$"}])
        if name == "playbooks":
            return _SequentialQuery(
                lambda: RuntimeError("supabase unavailable")
                if self.fail_playbooks
                else [
                    {
                        "payload": {
                            "locators": {
                                "hero": [
                                    {"type": "role", "value": "heading+Example", "priority": 1, "confidence": 0.9},
                                ]
                            },
                            "validation": {"validation_count": 1, "success_rate": 1.0, "status": "healthy"},
                            "registry": {"scope": "public", "tenant_id": None},
                            "promotion": {"review_status": "approved"},
                        },
                        "confidence": 0.9,
                        "variant_key": "desktop_enUS_loggedout",
                        "version": 1,
                    }
                ]
            )
        raise AssertionError(f"Unexpected table requested: {name}")


class _PlaybookFetchSupabase:
    def __init__(self, playbook_rows):
        self.playbook_rows = playbook_rows

    def table(self, name):
        if name == "playbooks":
            return _FakeQuery(self.playbook_rows)
        raise AssertionError(f"Unexpected table requested: {name}")


class RegistryHelpersTest(unittest.TestCase):
    def test_match_route_prefers_matching_pattern(self):
        routes = [
            {"id": 1, "route_key": "home", "path_pattern": r"^/$"},
            {"id": 2, "route_key": "job_detail", "path_pattern": r"^/jobs/[^/]+$"},
        ]

        matched = AtlasRegistry.match_route("https://example.com/jobs/123", routes)

        self.assertIsNotNone(matched)
        self.assertEqual(matched["route_key"], "job_detail")

    def test_match_route_falls_back_to_first_route(self):
        routes = [
            {"id": 1, "route_key": "fallback", "path_pattern": r"["},
            {"id": 2, "route_key": "other", "path_pattern": r"^/x$"},
        ]

        matched = AtlasRegistry.match_route("https://example.com/unknown", routes)

        self.assertIsNotNone(matched)
        self.assertEqual(matched["route_key"], "fallback")

    def test_build_elements_uses_lowest_priority_locator(self):
        payload = {
            "locators": {
                "job_title": [
                    {"type": "css", "value": ".title", "priority": 5, "confidence": 0.7},
                    {"type": "role", "value": "heading+Job title", "priority": 1, "confidence": 0.9},
                ]
            }
        }

        elements = AtlasRegistry.build_elements(payload)

        self.assertEqual(
            elements["job_title"],
            {"type": "role", "selector": "heading+Job title", "confidence": 0.9},
        )

    def test_path_signature_normalizes_dynamic_segments(self):
        self.assertEqual(path_signature("/jobs/12345"), "/jobs/:id")
        self.assertEqual(path_signature("/jobs/550e8400-e29b-41d4-a716-446655440000"), "/jobs/:id")

    def test_path_pattern_from_signature_matches_dynamic_route(self):
        pattern = path_pattern_from_signature("/jobs/:id")

        self.assertRegex("/jobs/12345", pattern)
        self.assertRegex("/jobs/abcde", pattern)
        self.assertRegex("/jobs/abcde/", pattern)

    def test_match_route_tolerates_trailing_slash(self):
        routes = [
            {"id": 1, "route_key": "quotes_js", "path_pattern": r"^/js/?$"},
            {"id": 2, "route_key": "quotes_login", "path_pattern": r"^/login/?$"},
        ]

        matched = AtlasRegistry.match_route("https://quotes.toscrape.com/js/", routes)

        self.assertIsNotNone(matched)
        self.assertEqual(matched["route_key"], "quotes_js")

    def test_route_fingerprint_is_stable_for_same_structure(self):
        nodes = [
            {"role": "heading", "name": "Software Engineer", "level": 1},
            {"role": "link", "name": "Apply now", "level": 2},
        ]

        first = build_route_fingerprint(nodes, "https://example.com/jobs/123")
        second = build_route_fingerprint(nodes, "https://example.com/jobs/456")

        self.assertEqual(first["value"], second["value"])
        self.assertEqual(first["path_signature"], "/jobs/:id")

    def test_latest_validation_summary_prefers_history_table(self):
        registry = AtlasRegistry(
            _FakeSupabase([
                {
                    "validated_at": "2026-03-07T10:00:00+00:00",
                    "status": "healthy",
                    "success_rate": 1.0,
                    "success_count": 5,
                    "failure_count": 0,
                    "validation_count": 3,
                    "schema_version": 2,
                    "stored_fingerprint": "abc123",
                    "current_fingerprint": "abc123",
                    "fingerprint_match": True,
                    "locator_results": [{"element": "job_title"}],
                }
            ])
        )

        summary = registry._get_latest_validation_summary(
            playbook_payload={"validation": {"validation_count": 1}},
            site_id=1,
            route_id=2,
            task_id=3,
            variant_key="desktop_enUS_loggedout",
        )

        self.assertEqual(summary["last_validated_at"], "2026-03-07T10:00:00+00:00")
        self.assertEqual(summary["validation_count"], 3)
        self.assertEqual(summary["success_rate"], 1.0)
        self.assertEqual(summary["status"], "healthy")
        self.assertEqual(summary["schema_version"], 2)
        self.assertTrue(summary["fingerprint_match"])

    def test_normalize_inputtime_role_to_css_time_selector(self):
        registry = AtlasRegistry(_FakeSupabase())

        normalized = registry._normalize_selector_record({
            "type": "role",
            "selector": "role=InputTime+Preferred delivery time: ",
            "confidence": 0.9,
        })

        self.assertEqual(
            normalized,
            {"type": "css", "selector": "input[type=time]", "confidence": 0.9},
        )

    def test_normalize_text_role_like_selector_to_text_type(self):
        registry = AtlasRegistry(_FakeSupabase())

        normalized = registry._normalize_selector_record({
            "type": "role",
            "selector": "text[name='Example Domain']",
            "confidence": 0.8,
        })

        self.assertEqual(
            normalized,
            {"type": "text", "selector": "Example Domain", "confidence": 0.8},
        )

    def test_build_elements_drops_invalid_role_selectors(self):
        payload = {
            "locators": {
                "bad_element": [
                    {"type": "role", "value": "role=UnknownWidget+Thing", "priority": 1, "confidence": 0.9},
                ]
            }
        }

        elements = AtlasRegistry.build_elements(payload)

        self.assertEqual(elements, {})

    def test_quality_summary_promotes_trusted_playbooks(self):
        quality = AtlasRegistry._compute_quality_summary(
            confidence=0.82,
            validation={
                "validation_count": 4,
                "success_rate": 1.0,
                "status": "healthy",
            },
            telemetry={
                "outcomes": [
                    {"status": "success"},
                    {"status": "success"},
                ]
            },
        )

        self.assertEqual(quality["quality_status"], "trusted")
        self.assertTrue(quality["serveable"])
        self.assertGreaterEqual(quality["trust_score"], 0.8)

    def test_quality_summary_quarantines_weak_playbooks(self):
        quality = AtlasRegistry._compute_quality_summary(
            confidence=0.7,
            validation={
                "validation_count": 3,
                "success_rate": 0.4,
                "status": "degraded",
            },
            telemetry={
                "outcomes": [
                    {"status": "failed"},
                    {"status": "failed"},
                    {"status": "failed"},
                ]
            },
        )

        self.assertEqual(quality["quality_status"], "quarantined")
        self.assertFalse(quality["serveable"])

    def test_fetch_schema_skips_quarantined_playbooks(self):
        registry = AtlasRegistry(
            _PlaybookFetchSupabase(
                [
                    {
                        "confidence": 0.9,
                        "payload": {
                            "locators": {
                                "title": [
                                    {"type": "role", "value": "heading+Bad", "priority": 1, "confidence": 0.9},
                                ]
                            },
                            "validation": {
                                "validation_count": 3,
                                "success_rate": 0.4,
                                "status": "degraded",
                            },
                            "telemetry": {
                                "outcomes": [
                                    {"status": "failed"},
                                    {"status": "failed"},
                                    {"status": "failed"},
                                ]
                            },
                        },
                    },
                    {
                        "confidence": 0.7,
                        "payload": {
                            "locators": {
                                "title": [
                                    {"type": "role", "value": "heading+Good", "priority": 1, "confidence": 0.8},
                                ]
                            },
                            "validation": {
                                "validation_count": 2,
                                "success_rate": 1.0,
                                "status": "healthy",
                            },
                        },
                    },
                ]
            )
        )
        registry._find_route = lambda site, url: {"site_id": "site-1", "id": "route-1", "route_key": "home"}

        schema = registry.fetch_schema("example.com", "https://example.com/")

        self.assertIsNotNone(schema)
        self.assertEqual(schema["elements"]["title"]["selector"], "heading+Good")
        self.assertEqual(schema["quality"]["quality_status"], "verified")

    def test_fetch_schema_uses_cached_value_when_registry_read_fails(self):
        supabase = _ReadFallbackSupabase()
        registry = AtlasRegistry(supabase)

        first = registry.fetch_schema("example.com", "https://example.com/")

        self.assertIsNotNone(first)
        self.assertFalse(registry.read_degraded())

        supabase.fail_playbooks = True
        second = registry.fetch_schema("example.com", "https://example.com/")

        self.assertIsNotNone(second)
        self.assertEqual(second["elements"]["hero"]["selector"], "heading+Example")
        self.assertTrue(registry.read_degraded())

    def test_scope_filter_prefers_private_then_public(self):
        playbooks = [
            {"variant_key": "desktop_enUS_loggedout", "payload": {"registry": {"scope": "public", "tenant_id": None}}},
            {"variant_key": "desktop_enUS_loggedout", "payload": {"registry": {"scope": "private", "tenant_id": "tenant-a"}}},
            {"variant_key": "desktop_enUS_loggedout", "payload": {"registry": {"scope": "private", "tenant_id": "tenant-b"}}},
        ]

        scoped = AtlasRegistry._filter_playbooks_by_scope(playbooks, tenant_id="tenant-a", registry_scope="auto")

        self.assertEqual(scoped[0]["payload"]["registry"]["scope"], "private")
        self.assertEqual(scoped[0]["payload"]["registry"]["tenant_id"], "tenant-a")
        self.assertEqual(scoped[1]["payload"]["registry"]["scope"], "public")

    def test_high_value_public_domains_require_review(self):
        promotion = AtlasRegistry._build_promotion_state(
            site="github.com",
            registry_scope="public",
            tenant_id=None,
        )

        self.assertEqual(promotion["review_status"], "review_required")
        self.assertEqual(promotion["review_reason"], "domain_class:social_auth")

    def test_private_high_value_domains_skip_human_gate(self):
        promotion = AtlasRegistry._build_promotion_state(
            site="github.com",
            registry_scope="private",
            tenant_id="tenant-a",
        )

        self.assertEqual(promotion["review_status"], "approved")

    def test_domain_classification_uses_policy_groups(self):
        self.assertEqual(AtlasRegistry._classify_domain("github.com"), "social_auth")
        self.assertEqual(AtlasRegistry._classify_domain("greenhouse.io"), "job_board")
        self.assertEqual(AtlasRegistry._classify_domain("example.com"), "docs")
        self.assertEqual(AtlasRegistry._classify_domain("unknown-example.xyz"), "general")

    def test_auto_scope_conflict_prefers_stronger_public_memory_when_private_is_weak(self):
        registry = AtlasRegistry(_FakeSupabase())
        playbooks = [
            {
                "variant_key": "desktop_enUS_loggedout",
                "confidence": 0.55,
                "version": 1,
                "payload": {
                    "registry": {"scope": "private", "tenant_id": "tenant-a"},
                    "fingerprint": {"value": "private-1"},
                    "validation": {"validation_count": 1, "success_rate": 0.5, "status": "degraded"},
                },
            },
            {
                "variant_key": "desktop_enUS_loggedout",
                "confidence": 0.88,
                "version": 2,
                "payload": {
                    "registry": {"scope": "public", "tenant_id": None},
                    "fingerprint": {"value": "public-2"},
                    "validation": {"validation_count": 4, "success_rate": 1.0, "status": "healthy"},
                    "promotion": {"review_status": "approved"},
                },
            },
        ]

        ordered = registry._resolve_scope_conflicts(
            playbooks=playbooks,
            variant_key="desktop_enUS_loggedout",
            tenant_id="tenant-a",
            registry_scope="auto",
        )

        self.assertEqual(ordered[0]["payload"]["registry"]["scope"], "public")

    def test_explain_scope_conflict_reports_public_override_reason(self):
        registry = AtlasRegistry(_FakeSupabase())
        private_row = {
            "confidence": 0.55,
            "payload": {
                "registry": {"scope": "private", "tenant_id": "tenant-a"},
                "fingerprint": {"value": "private-1"},
                "validation": {"validation_count": 1, "success_rate": 0.5, "status": "degraded"},
            },
        }
        public_row = {
            "confidence": 0.92,
            "payload": {
                "registry": {"scope": "public", "tenant_id": None},
                "fingerprint": {"value": "public-2"},
                "validation": {"validation_count": 4, "success_rate": 1.0, "status": "healthy"},
                "promotion": {"review_status": "approved"},
            },
        }

        explanation = registry._explain_scope_conflict(
            private_row=private_row,
            public_row=public_row,
            resolved_row=public_row,
        )

        self.assertEqual(explanation["winner"], "public")
        self.assertEqual(explanation["reason"], "public_memory_stronger_than_private_on_fingerprint_conflict")

    def test_diff_locator_sets_reports_changed_elements(self):
        registry = AtlasRegistry(_FakeSupabase())
        private_row = {
            "payload": {
                "locators": {
                    "login_button": [{"type": "role", "value": "button+Sign in", "priority": 1, "confidence": 0.7}],
                }
            }
        }
        public_row = {
            "payload": {
                "locators": {
                    "login_button": [{"type": "role", "value": "button+Sign in to GitHub", "priority": 1, "confidence": 0.9}],
                }
            }
        }

        diffs = registry._diff_locator_sets(private_row, public_row)

        self.assertEqual(diffs[0]["element"], "login_button")
        self.assertEqual(diffs[0]["private"]["selector"], "button+Sign in")
        self.assertEqual(diffs[0]["public"]["selector"], "button+Sign in to GitHub")

    def test_revalidation_due_reason_covers_status_and_age(self):
        self.assertEqual(
            AtlasRegistry._revalidation_due_reason({"status": "degraded"}, max_age_hours=24),
            "status:degraded",
        )
        self.assertTrue(
            AtlasRegistry._revalidation_due_reason({"last_validated_at": "2026-03-01T10:00:00+00:00"}, max_age_hours=24).startswith("age:")
        )
        self.assertEqual(
            AtlasRegistry._revalidation_due_reason({}, max_age_hours=24),
            "missing_validation",
        )

    def test_locator_priority_prefers_structured_selectors(self):
        self.assertEqual(AtlasRegistry._locator_priority({"type": "data_testid", "selector": "submit"}), 1)
        self.assertEqual(AtlasRegistry._locator_priority({"type": "text", "selector": "Apply now"}), 7)


class ModelSurfaceTest(unittest.TestCase):
    def test_validation_report_carries_fingerprint_state(self):
        report = ValidationReport(
            site="example.com",
            url="https://example.com/",
            route_key="home",
            status="stale",
            source="registry",
            validation_count=2,
            success_count=1,
            failure_count=1,
            success_rate=0.5,
            last_validated_at="2026-03-07T10:00:00+00:00",
            schema_version=3,
            stored_fingerprint="old123",
            current_fingerprint="new456",
            fingerprint_match=False,
            locator_results=[],
            message="fingerprint drift",
        )

        self.assertEqual(report.schema_version, 3)
        self.assertFalse(report.fingerprint_match)

    def test_benchmark_result_can_include_failed_locator_detail(self):
        result = BenchmarkResult(
            name="example",
            site="example.com",
            url="https://example.com/",
            category="general",
            first_source="registry",
            second_source="registry",
            first_tokens=0,
            second_tokens=0,
            warm_registry_hit=True,
            validation_status="degraded",
            locator_count=2,
            fingerprint_match=True,
            schema_version=1,
            elapsed_ms=100,
            validation_message="some locators failed",
            failed_locators=[
                {
                    "element": "cta_link",
                    "selector_type": "role",
                    "selector": "link+More information",
                    "matched": False,
                    "visible": False,
                    "match_count": 0,
                    "actionable": False,
                    "ambiguous": False,
                    "error": "",
                }
            ],
        )

        self.assertEqual(result.failed_locators[0]["element"], "cta_link")

    def test_locator_resolution_can_mark_ambiguity(self):
        resolution = LocatorResolution(
            element="apply_button",
            selector_type="role",
            selector="button+Apply",
            matched=True,
            visible=True,
            match_count=3,
            actionable=False,
            ambiguous=True,
        )

        self.assertTrue(resolution.ambiguous)
        self.assertFalse(resolution.actionable)

    def test_playbook_record_exposes_quality_fields(self):
        from agentatlas.models import PlaybookRecord

        playbook = PlaybookRecord(
            site="example.com",
            url="https://example.com",
            route_key="home",
            task_key="generic_extract",
            variant_key="desktop_enUS_loggedout",
            confidence=0.82,
            elements={},
            source="registry",
            trust_score=0.88,
            quality_status="trusted",
            serveable=True,
        )

        self.assertEqual(playbook.quality_status, "trusted")
        self.assertTrue(playbook.serveable)
        self.assertEqual(playbook.trust_score, 0.88)

    def test_role_selector_parser_accepts_multiple_formats(self):
        parser_owner = Atlas.__new__(Atlas)

        self.assertEqual(parser_owner._parse_role_selector("textbox+Customer name"), ("textbox", "Customer name"))
        self.assertEqual(parser_owner._parse_role_selector("role=textbox+Customer name: "), ("textbox", "Customer name:"))
        self.assertEqual(parser_owner._parse_role_selector("heading[name='Example Domain']"), ("heading", "Example Domain"))

    def test_execute_is_demoted_from_main_sdk_surface(self):
        atlas = Atlas(api_url="https://api.agentatlas.dev", api_key="secret", use_api=True)

        with self.assertRaises(RuntimeError):
            __import__("asyncio").run(
                atlas.execute(site="example.com", url="https://example.com", task="click the hero")
            )

    def test_agent_executor_retains_execution_surface(self):
        executor = AgentExecutor.__new__(AgentExecutor)

        self.assertTrue(hasattr(executor, "execute"))

    def test_variant_inference_uses_environment(self):
        import os

        atlas = Atlas.__new__(Atlas)
        original_locale = os.environ.get("AGENTATLAS_LOCALE")
        original_region = os.environ.get("AGENTATLAS_REGION")
        original_device = os.environ.get("AGENTATLAS_DEVICE_CLASS")
        original_auth = os.environ.get("AGENTATLAS_AUTH_STATE")
        try:
            os.environ["AGENTATLAS_LOCALE"] = "en-US"
            os.environ["AGENTATLAS_REGION"] = "us"
            os.environ["AGENTATLAS_DEVICE_CLASS"] = "mobile"
            os.environ["AGENTATLAS_AUTH_STATE"] = "loggedin"
            self.assertEqual(
                atlas.infer_variant_key("https://example.com/dashboard"),
                "mobile_enUS_loggedin_us",
            )
        finally:
            if original_locale is None:
                os.environ.pop("AGENTATLAS_LOCALE", None)
            else:
                os.environ["AGENTATLAS_LOCALE"] = original_locale
            if original_region is None:
                os.environ.pop("AGENTATLAS_REGION", None)
            else:
                os.environ["AGENTATLAS_REGION"] = original_region
            if original_device is None:
                os.environ.pop("AGENTATLAS_DEVICE_CLASS", None)
            else:
                os.environ["AGENTATLAS_DEVICE_CLASS"] = original_device
            if original_auth is None:
                os.environ.pop("AGENTATLAS_AUTH_STATE", None)
            else:
                os.environ["AGENTATLAS_AUTH_STATE"] = original_auth


if __name__ == "__main__":
    unittest.main()
