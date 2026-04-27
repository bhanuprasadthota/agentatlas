import asyncio
import json
import os
import time
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from agentatlas import Atlas


load_dotenv(Path(__file__).with_name(".env"))


REQUIRED_ENV_VARS = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "OPENAI_API_KEY",
)


@dataclass(frozen=True)
class BenchmarkWorkflow:
    name: str
    site: str
    url: str
    notes: str
    category: str = "general"


@dataclass
class BenchmarkResult:
    name: str
    site: str
    url: str
    category: str
    first_source: str
    second_source: str
    first_tokens: int
    second_tokens: int
    warm_registry_hit: bool
    validation_status: str
    locator_count: int
    fingerprint_match: bool | None
    schema_version: int | None
    elapsed_ms: int
    validation_message: str = ""
    failed_locators: list[dict] | None = None
    error: str = ""


BENCHMARK_WORKFLOWS = [
    BenchmarkWorkflow(
        name="httpbin_form",
        site="httpbin.org",
        url="https://httpbin.org/forms/post",
        notes="Stable public form route used to measure repeat schema lookup reliability.",
        category="dynamic_form",
    ),
    BenchmarkWorkflow(
        name="example_home",
        site="example.com",
        url="https://example.com/",
        notes="Minimal static page for warm-start registry hit behavior.",
        category="minimal_static",
    ),
    BenchmarkWorkflow(
        name="iana_example",
        site="iana.org",
        url="https://www.iana.org/domains/example",
        notes="Simple informational page with stable public structure.",
        category="content_page",
    ),
    BenchmarkWorkflow(
        name="github_login",
        site="github.com",
        url="https://github.com/login",
        notes="Public auth wall page to measure login-form locator memory.",
        category="auth_wall",
    ),
    BenchmarkWorkflow(
        name="quotes_login",
        site="quotes.toscrape.com",
        url="https://quotes.toscrape.com/login",
        notes="Simple login page useful for repeated labels and form-field stability.",
        category="auth_wall",
    ),
    BenchmarkWorkflow(
        name="books_listing",
        site="books.toscrape.com",
        url="https://books.toscrape.com/",
        notes="Repeated labels and repeated CTA buttons to measure ambiguity handling.",
        category="repeated_labels",
    ),
    BenchmarkWorkflow(
        name="quotes_js",
        site="quotes.toscrape.com",
        url="https://quotes.toscrape.com/js/",
        notes="Client-rendered route that exercises delayed hydration and JS rendering.",
        category="delayed_hydration",
    ),
    # --- Extended verticals ---
    BenchmarkWorkflow(
        name="wikipedia_article",
        site="en.wikipedia.org",
        url="https://en.wikipedia.org/wiki/Python_(programming_language)",
        notes="Dense long-form content page with stable heading/section structure.",
        category="content_page",
    ),
    BenchmarkWorkflow(
        name="hn_frontpage",
        site="news.ycombinator.com",
        url="https://news.ycombinator.com/",
        notes="Minimal server-rendered listing page; near-zero JS, very stable HTML.",
        category="repeated_labels",
    ),
    BenchmarkWorkflow(
        name="lever_jobs",
        site="jobs.lever.co",
        url="https://jobs.lever.co/vercel",
        notes="Lever job board; parallel to Greenhouse vertical for ATS diversity.",
        category="job_board",
    ),
    BenchmarkWorkflow(
        name="arxiv_abstract",
        site="arxiv.org",
        url="https://arxiv.org/abs/2303.08774",
        notes="Academic abstract page with structured metadata — stable doi/title/author layout.",
        category="content_page",
    ),
    BenchmarkWorkflow(
        name="pypi_package",
        site="pypi.org",
        url="https://pypi.org/project/requests/",
        notes="Package registry detail page with version table, metadata sidebar, and install command.",
        category="content_page",
    ),
    BenchmarkWorkflow(
        name="reddit_search",
        site="www.reddit.com",
        url="https://www.reddit.com/search/?q=browser+automation&type=link",
        notes="JS-rendered search results page; tests delayed hydration and repeated post cards.",
        category="delayed_hydration",
    ),
]


def integration_enabled() -> bool:
    return os.getenv("AGENTATLAS_RUN_INTEGRATION", "").strip() == "1"


def integration_env_missing() -> list[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]


async def run_workflow(atlas: Atlas, workflow: BenchmarkWorkflow) -> BenchmarkResult:
    started_at = time.perf_counter()
    try:
        first_schema = await atlas.get_schema(site=workflow.site, url=workflow.url)
        second_schema = await atlas.get_schema(site=workflow.site, url=workflow.url)
        validation = await atlas.validate(
            site=workflow.site,
            url=workflow.url,
            persist=False,
            headless=True,
            relearn_on_degraded=True,
        )
        failed_locators = [
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
            for item in validation.locator_results
            if not item.actionable
        ]

        return BenchmarkResult(
            name=workflow.name,
            site=workflow.site,
            url=workflow.url,
            category=workflow.category,
            first_source=first_schema.source,
            second_source=second_schema.source,
            first_tokens=first_schema.tokens_used,
            second_tokens=second_schema.tokens_used,
            warm_registry_hit=(second_schema.source == "registry" and second_schema.tokens_used == 0),
            validation_status=validation.status,
            locator_count=len(second_schema.elements),
            fingerprint_match=validation.fingerprint_match,
            schema_version=validation.schema_version,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            validation_message=validation.message,
            failed_locators=failed_locators,
        )
    except Exception as exc:
        return BenchmarkResult(
            name=workflow.name,
            site=workflow.site,
            url=workflow.url,
            category=workflow.category,
            first_source="failed",
            second_source="failed",
            first_tokens=0,
            second_tokens=0,
            warm_registry_hit=False,
            validation_status="failed",
            locator_count=0,
            fingerprint_match=None,
            schema_version=None,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            validation_message="Workflow execution failed.",
            failed_locators=[],
            error=str(exc),
        )


async def run_benchmark_suite(workflows: list[BenchmarkWorkflow] | None = None) -> list[BenchmarkResult]:
    atlas = Atlas()
    results = []
    for workflow in workflows or BENCHMARK_WORKFLOWS:
        results.append(await run_workflow(atlas, workflow))
    return results


class WarmStartReliabilityIntegration(unittest.TestCase):
    @unittest.skipUnless(integration_enabled(), "Set AGENTATLAS_RUN_INTEGRATION=1 to run integration benchmarks.")
    def test_benchmark_workflows(self):
        missing = integration_env_missing()
        if missing:
            self.skipTest(f"Missing environment variables: {', '.join(missing)}")

        results = asyncio.run(run_benchmark_suite())
        failed = [
            result for result in results
            if not result.warm_registry_hit or result.validation_status == "failed"
        ]

        self.assertFalse(
            failed,
            json.dumps([asdict(result) for result in results], indent=2),
        )


def main() -> int:
    if not integration_enabled():
        print("Set AGENTATLAS_RUN_INTEGRATION=1 to run the live benchmark harness.")
        return 1

    missing = integration_env_missing()
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}")
        return 1

    results = asyncio.run(run_benchmark_suite())
    payload = [asdict(result) for result in results]
    print(json.dumps(payload, indent=2))

    output_path = os.getenv("AGENTATLAS_BENCHMARK_OUTPUT", "").strip()
    if output_path:
        Path(output_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    atlas = Atlas()
    persisted = atlas.registry.persist_benchmark_run(
        suite_name="warm_start_reliability",
        results=payload,
        metadata={
            "runner": "test_execute.py",
            "workflow_names": [workflow.name for workflow in BENCHMARK_WORKFLOWS],
        },
        tenant_id=atlas.tenant_id,
    )
    if not persisted:
        print("WARNING: benchmark run was not persisted to benchmark_runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
