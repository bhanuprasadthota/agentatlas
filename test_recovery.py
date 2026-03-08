import asyncio
import logging
import unittest
import warnings

from agentatlas.atlas import Atlas
from agentatlas.models import LocatorResolution, PlaybookRecord
from agentatlas.registry import AtlasRegistry
from agentatlas.versioning import API_VERSION, SDK_VERSION, STABLE_SURFACE


class RecoveryLeaseTest(unittest.TestCase):
    def tearDown(self):
        AtlasRegistry._recovery_leases.clear()

    def test_registry_recovery_leases_are_exclusive(self):
        acquired_first, first = AtlasRegistry.start_recovery(
            site="example.com",
            route_key="login",
            task_key="generic_extract",
            variant_key="desktop_enUS_loggedout",
            tenant_id=None,
            registry_scope="public",
            reason="stale",
            ttl_seconds=60,
        )
        acquired_second, second = AtlasRegistry.start_recovery(
            site="example.com",
            route_key="login",
            task_key="generic_extract",
            variant_key="desktop_enUS_loggedout",
            tenant_id=None,
            registry_scope="public",
            reason="stale",
            ttl_seconds=60,
        )

        self.assertTrue(acquired_first)
        self.assertFalse(acquired_second)
        self.assertEqual(first["owner"], second["owner"])

        AtlasRegistry.finish_recovery(first)

        acquired_third, _third = AtlasRegistry.start_recovery(
            site="example.com",
            route_key="login",
            task_key="generic_extract",
            variant_key="desktop_enUS_loggedout",
            tenant_id=None,
            registry_scope="public",
            reason="stale",
            ttl_seconds=60,
        )
        self.assertTrue(acquired_third)

    def test_version_contract_is_exposed(self):
        self.assertEqual(SDK_VERSION, "0.4.0")
        self.assertEqual(API_VERSION, "v1")
        self.assertIn("Atlas.get_schema", STABLE_SURFACE)


class _RecoveringSchemaRegistry:
    def fetch_schema(self, *_args, **_kwargs):
        return None

    def get_route_playbook_snapshot(self, *_args, **_kwargs):
        return {"route_key": "login", "status": "stale", "payload": {}}

    def start_recovery(self, **_kwargs):
        return False, {"reason": "stale", "owner": "worker-1"}

    def finish_recovery(self, _lease):
        return None


class _ValidationRecoveryRegistry:
    def get_playbook(self, *_args, **_kwargs):
        return PlaybookRecord(
            site="example.com",
            url="https://example.com/login",
            route_key="login",
            task_key="generic_extract",
            variant_key="desktop_enUS_loggedout",
            confidence=0.7,
            elements={"login_button": {"type": "role", "selector": "button+Login", "confidence": 0.9}},
            source="registry",
            schema_version=1,
            fingerprint="old-fingerprint",
            validation_count=1,
        )

    def fetch_schema(self, *_args, **_kwargs):
        return {
            "route_key": "login",
            "confidence": 0.7,
            "elements": {"login_button": {"type": "role", "selector": "button+Login", "confidence": 0.9}},
        }

    def persist_validation(self, *_args, **_kwargs):
        return True

    def start_recovery(self, **_kwargs):
        return False, {"reason": "validation:stale", "owner": "worker-2"}

    def finish_recovery(self, _lease):
        return None


class _RegistryUnavailable:
    def fetch_schema(self, *_args, **_kwargs):
        return None

    def get_route_playbook_snapshot(self, *_args, **_kwargs):
        return {"route_key": "login", "status": None, "payload": {}}

    def read_degraded(self):
        return True


class _PendingReviewRegistry:
    def fetch_schema(self, *_args, **_kwargs):
        return None

    def get_route_playbook_snapshot(self, *_args, **_kwargs):
        return {
            "route_key": "login",
            "status": "active",
            "payload": {
                "registry": {"scope": "public", "tenant_id": None},
                "promotion": {"review_status": "review_required"},
            },
            "confidence": 0.61,
        }

    def read_degraded(self):
        return False


class CoordinatedRecoverySurfaceTest(unittest.TestCase):
    def _make_atlas(self, registry):
        atlas = object.__new__(Atlas)
        atlas.use_api = False
        atlas.registry = registry
        atlas.registry_scope = "auto"
        atlas.tenant_id = None
        atlas.logger = logging.getLogger("agentatlas-test")
        atlas.api_url = None
        atlas.api_key = None
        atlas.api_timeout = 10.0
        atlas._session_cache = {}
        atlas.client = None
        atlas.sb = None
        return atlas

    def test_get_schema_returns_recovering_when_cold_start_already_in_progress(self):
        atlas = self._make_atlas(_RecoveringSchemaRegistry())

        schema = asyncio.run(atlas.get_schema(site="example.com", url="https://example.com/login"))

        self.assertEqual(schema.status, "recovering")
        self.assertEqual(schema.source, "recovery_pending")
        self.assertEqual(schema.recovery_state, "relearning")
        self.assertEqual(schema.route_key, "login")

    def test_get_schema_returns_registry_unavailable_when_backing_store_is_degraded(self):
        atlas = self._make_atlas(_RegistryUnavailable())

        schema = asyncio.run(atlas.get_schema(site="example.com", url="https://example.com/login"))

        self.assertEqual(schema.status, "registry_unavailable")
        self.assertEqual(schema.source, "registry_unavailable")
        self.assertEqual(schema.recovery_state, "degraded_read")

    def test_get_schema_returns_pending_review_for_public_review_queue_entries(self):
        atlas = self._make_atlas(_PendingReviewRegistry())

        schema = asyncio.run(atlas.get_schema(site="github.com", url="https://github.com/login"))

        self.assertEqual(schema.status, "pending_review")
        self.assertEqual(schema.source, "review_queue")
        self.assertEqual(schema.recovery_state, "review_required")
        self.assertIn("awaiting review approval", schema.message)

    def test_validate_returns_in_progress_when_relearn_is_already_running(self):
        atlas = self._make_atlas(_ValidationRecoveryRegistry())

        async def fake_validate_elements(_url, _elements, headless=True):
            return (
                [
                    LocatorResolution(
                        element="login_button",
                        selector_type="role",
                        selector="button+Login",
                        matched=False,
                        visible=False,
                        match_count=0,
                        actionable=False,
                        ambiguous=False,
                        error="",
                    )
                ],
                "new-fingerprint",
            )

        atlas._validate_elements = fake_validate_elements

        report = asyncio.run(atlas.validate(site="example.com", url="https://example.com/login"))

        self.assertEqual(report.status, "stale")
        self.assertEqual(report.recovery_state, "in_progress")
        self.assertIn("Recovery already in progress", report.message)

    def test_get_schema_returns_timeout_when_learn_budget_expires(self):
        class _TimeoutRegistry(_RecoveringSchemaRegistry):
            def start_recovery(self, **_kwargs):
                return True, {"key": "lease-1", "owner": "worker-1", "reason": "cold_start"}

        atlas = self._make_atlas(_TimeoutRegistry())
        atlas.learn_timeout_seconds = 0.01

        async def fake_learn_site(_site, _url):
            await asyncio.sleep(0.05)
            return {"route_key": "login", "elements": {}, "tokens_used": 10}

        atlas._learn_site = fake_learn_site

        schema = asyncio.run(atlas.get_schema(site="example.com", url="https://example.com/login"))

        self.assertEqual(schema.status, "timeout")
        self.assertEqual(schema.source, "timeout")
        self.assertEqual(schema.recovery_state, "timed_out")
        self.assertIn("timed out", schema.message)

    def test_validate_surfaces_timeout_when_learning_missing_schema_times_out(self):
        class _TimeoutValidationRegistry:
            def get_playbook(self, *_args, **_kwargs):
                return None

            def fetch_schema(self, *_args, **_kwargs):
                return None

        atlas = self._make_atlas(_TimeoutValidationRegistry())

        async def fake_get_schema(*_args, **_kwargs):
            from agentatlas.models import SiteSchema

            return SiteSchema(
                site="example.com",
                url="https://example.com/login",
                route_key="login",
                status="timeout",
                confidence=0.0,
                elements={},
                source="timeout",
                tokens_used=0,
                message="Schema learning timed out after 0.1s.",
                recovery_state="timed_out",
            )

        atlas.get_schema = fake_get_schema

        report = asyncio.run(atlas.validate(site="example.com", url="https://example.com/login"))

        self.assertEqual(report.status, "timeout")
        self.assertEqual(report.recovery_state, "timed_out")
        self.assertEqual(report.source, "timeout")

    def test_validate_surfaces_pending_review_when_public_schema_is_queued(self):
        class _PendingReviewValidationRegistry:
            def get_playbook(self, *_args, **_kwargs):
                return None

            def fetch_schema(self, *_args, **_kwargs):
                return None

        atlas = self._make_atlas(_PendingReviewValidationRegistry())

        async def fake_get_schema(*_args, **_kwargs):
            from agentatlas.models import SiteSchema

            return SiteSchema(
                site="github.com",
                url="https://github.com/login",
                route_key="login",
                status="pending_review",
                confidence=0.61,
                elements={},
                source="review_queue",
                tokens_used=0,
                message="Schema exists in the public registry but is awaiting review approval.",
                recovery_state="review_required",
            )

        atlas.get_schema = fake_get_schema

        report = asyncio.run(atlas.validate(site="github.com", url="https://github.com/login"))

        self.assertEqual(report.status, "pending_review")
        self.assertEqual(report.source, "review_queue")
        self.assertEqual(report.recovery_state, "review_required")

    def test_wait_for_page_settle_waits_for_stable_signature(self):
        atlas = self._make_atlas(_RecoveringSchemaRegistry())

        class _FakePage:
            def __init__(self):
                self.signatures = iter(
                    [
                        "loading::/jobs::3::a",
                        "interactive::/jobs::5::a|b",
                        "complete::/jobs::7::a|b|c",
                        "complete::/jobs::7::a|b|c",
                        "complete::/jobs::7::a|b|c",
                    ]
                )
                self.waits = 0

            async def wait_for_load_state(self, *_args, **_kwargs):
                return None

            async def evaluate(self, _script):
                return next(self.signatures)

            async def wait_for_timeout(self, _ms):
                self.waits += 1

        page = _FakePage()
        asyncio.run(atlas._wait_for_page_settle(page, timeout_ms=3000, stable_cycles=2, poll_ms=10))

        self.assertGreaterEqual(page.waits, 3)

    def test_execute_emits_deprecation_warning_before_error(self):
        atlas = self._make_atlas(_RecoveringSchemaRegistry())

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with self.assertRaises(RuntimeError):
                asyncio.run(atlas.execute(site="example.com", url="https://example.com", task="x"))

        self.assertTrue(any("Atlas.execute() is deprecated" in str(item.message) for item in caught))
