# AgentAtlas

**Shared web interaction memory with validation.**

AgentAtlas is the registry layer for browser agents. It learns stable page locators once, stores them as reusable schemas/playbooks, and validates them over time so other agents can reuse web interaction memory instead of repeatedly perceiving the same pages.

## Why install it

If you scrape or automate the same web route more than once, AgentAtlas gives you a reusable memory layer for that route.

Typical pattern:

- first run: learn a job board page and pay the perception cost once
- later runs: hit the registry and reuse the learned locators with `0` lookup tokens
- over time: validate, stale-detect, review, and repair the locator set instead of relearning from scratch

The clearest first use case is job boards:

- Greenhouse boards
- Lever boards
- recruiting detail pages that your pipeline revisits every day

## First demo

Run the bundled job-board warm-start example:

```bash
python3 examples/job_board_warm_start.py
```

The example defaults to `private` scope with a local demo tenant so the second run can actually warm-hit immediately without waiting for public review approval on job-board domains.

By default it targets a public Greenhouse board. You can switch boards or force a specific URL:

```bash
AGENTATLAS_DEMO_BOARD=lever python3 examples/job_board_warm_start.py
AGENTATLAS_DEMO_URL=https://boards.greenhouse.io/anthropic python3 examples/job_board_warm_start.py
```

Relevant knobs:

```bash
AGENTATLAS_DEMO_REGISTRY_SCOPE=private
AGENTATLAS_DEMO_TENANT_ID=demo-local
```

What it does:

1. calls `get_schema()` for a job board page
2. calls `get_schema()` for the same page again
3. fetches the current playbook
4. prints whether the second lookup was a warm registry hit

Example output shape:

```json
{
  "site": "boards.greenhouse.io",
  "registry_scope": "private",
  "warm_hit": true,
  "element_count": 6,
  "first_lookup": {
    "source": "llm_learned",
    "tokens_used": 1432
  },
  "second_lookup": {
    "source": "registry",
    "tokens_used": 0
  }
}
```

That is the core product value: repeated page understanding becomes shared memory instead of repeated LLM spend.

If you switch the demo to `public` scope on a sensitive domain like Greenhouse, new learns will enter `review_required` and the second run will not warm-hit until approved. That is expected trust behavior, not a demo bug.

## Product focus

- Shared schema registry for web pages and routes
- Locator memory that can be reused across agents and teams
- Validation metadata so consumers can trust freshness and success rate
- Optional learning path for cold-start pages

## Module layout

The SDK is now split by responsibility:

- [`agentatlas/atlas.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/atlas.py): main facade and direct/hosted dispatch
- [`agentatlas/client.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/client.py): hosted API client, retries, typed response parsing
- [`agentatlas/browser_runtime.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/browser_runtime.py): browser learning, validation, and execution runtime
- [`agentatlas/executor.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/executor.py): explicit browser execution tooling for internal/operator use
- [`agentatlas/registry.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/registry.py): playbook persistence and route lookup
- [`agentatlas/registry_quality.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/registry_quality.py): trust, normalization, scope conflict logic
- [`agentatlas/registry_review.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/registry_review.py): review queue, audit, promotion, route diffs
- [`agentatlas/registry_benchmarks.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/registry_benchmarks.py): benchmark history and scheduled revalidation candidates

## How it works

```text
Cold page -> learn schema -> save locators -> validate over time
Warm page -> fetch trusted locators -> skip repeated perception cost
```

## Install

```bash
pip install agentatlas
playwright install chromium
```

If you want to use the hosted API instead of direct Supabase/OpenAI access:

```bash
export AGENTATLAS_API_URL=https://your-agentatlas-api.example.com
export AGENTATLAS_API_KEY=your-api-key
python3 examples/job_board_warm_start.py
```

## Core API

```python
from agentatlas import Atlas

atlas = Atlas()

schema = await atlas.get_schema(
    site="greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
)

playbook = await atlas.get_playbook(
    site="greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
)

report = await atlas.validate(
    site="greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
)

locator = await atlas.resolve_locator(
    site="greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
    element_name="job_title",
)
```

## Job listing extraction example

This is the simplest real workload where AgentAtlas starts paying for itself quickly:

```python
import asyncio

from agentatlas import Atlas


async def main():
    atlas = Atlas()
    url = "https://boards.greenhouse.io/anthropic"

    first = await atlas.get_schema(site="boards.greenhouse.io", url=url)
    second = await atlas.get_schema(site="boards.greenhouse.io", url=url)
    playbook = await atlas.get_playbook(site="boards.greenhouse.io", url=url)

    print("first:", first.source, first.tokens_used)
    print("second:", second.source, second.tokens_used)
    print("warm hit:", second.source == "registry" and second.tokens_used == 0)
    print("tracked elements:", sorted((playbook.elements or {}).keys()))


asyncio.run(main())
```

For a pipeline that revisits the same boards every day, that warm-start behavior is the whole point.

`Atlas.execute()` is intentionally no longer part of the main product surface. If you still need browser execution for operator workflows or cold-start collection, use [`AgentExecutor`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/executor.py:1) explicitly.

## Returned signals

- `schema.elements`: normalized locator map for a route
- `playbook.validation_count`: how many validation runs have been recorded
- `playbook.success_rate`: last stored locator health signal
- `playbook.schema_version`: current locator-set version for the route/task variant
- `playbook.fingerprint`: active route fingerprint used for drift detection
- `playbook.trust_score`: evidence-based trust score used for ranking
- `playbook.quality_status`: `candidate`, `verified`, `trusted`, or `quarantined`
- `playbook.serveable`: whether the playbook can currently be served for reuse
- `playbook.registry_scope`: `public` or `private`
- `playbook.review_status`: review/promotion state for high-value public domains
- `report.locator_results`: per-locator validation details
- `report.status`: `healthy`, `degraded`, `stale`, or `failed`

## Environment variables

```bash
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_key
OPENAI_API_KEY=your_key
AGENTATLAS_API_KEY=optional_shared_api_key
AGENTATLAS_API_KEYS=optional_comma_separated_keys
AGENTATLAS_TENANT_API_KEYS=optional_semicolon_separated_tenant_key_map
AGENTATLAS_API_URL=optional_hosted_api_base_url
AGENTATLAS_TENANT_ID=optional_tenant_id_for_hosted_sdk_mode
AGENTATLAS_REGISTRY_SCOPE=optional_registry_scope_default
AGENTATLAS_DEVICE_CLASS=optional_variant_inference_device
AGENTATLAS_LOCALE=optional_variant_inference_locale
AGENTATLAS_AUTH_STATE=optional_variant_inference_auth_state
AGENTATLAS_REGION=optional_variant_inference_region
AGENTATLAS_DOMAIN_CLASS_POLICIES=optional_domain_class_review_policy_map
AGENTATLAS_REVIEWER_ROLES=optional_reviewer_role_map
```

If `AGENTATLAS_API_URL` is set in the SDK, `Atlas.get_schema()`, `Atlas.get_playbook()`, `Atlas.resolve_locator()`, `Atlas.validate()`, and `Atlas.record_outcome()` can use the hosted API instead of direct Supabase/OpenAI access.
Hosted mode also supports review/admin methods:

- `Atlas.list_review_queue()`
- `Atlas.list_review_audit()`
- `Atlas.promote_playbook()`
- `Atlas.get_route_scope_diff()`

Minimal hosted API smoke test:

```bash
curl -X POST http://127.0.0.1:8000/v1/schema/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: single-shared-key" \
  -d '{"site":"boards.greenhouse.io","url":"https://boards.greenhouse.io/anthropic"}'
```

## Registry scopes

AgentAtlas now supports explicit memory scopes:

- `public`: shared memory reusable across tenants
- `private`: tenant-isolated memory
- `auto`: private-first, public-fallback lookup

Private writes require a tenant id. High-value domains learned into the public registry are held for review before they become serveable.

When both private and public memory exist for the same route, AgentAtlas now resolves conflicts explicitly:

- `auto` mode prefers private memory by default
- if private and public fingerprints disagree, stronger validated public memory can win
- weak private memory no longer automatically overrides trustworthy public memory

## Variant inference

You no longer need to handcraft `variant_key` for every call. The SDK infers a variant from environment context using:

- `AGENTATLAS_DEVICE_CLASS`
- `AGENTATLAS_LOCALE`
- `AGENTATLAS_AUTH_STATE`
- `AGENTATLAS_REGION`

Example inferred key:

```text
mobile_enUS_loggedin_us
```

You can still override `variant_key` explicitly when needed.

## Approval policy

Public memory approval is now driven by domain class policy, not a fixed hardcoded allowlist.

Default classes:

- `social_auth`
- `job_board`
- `commerce`
- `docs`
- `general`

Default policy map:

```text
social_auth:review_required;job_board:review_required;commerce:review_required;docs:auto_approve;general:auto_approve
```

Override it with `AGENTATLAS_DOMAIN_CLASS_POLICIES`.

Reviewer access for promotion/rejection can be controlled with:

```text
AGENTATLAS_REVIEWER_ROLES=ops@agentatlas.ai:admin;qa@agentatlas.ai:reviewer;viewer@agentatlas.ai:viewer
```

## Supabase schema

Apply the migration in [`supabase/migrations/20260307_create_validation_runs.sql`](/Users/bhanuprasadthota/Desktop/AgentAtlas/supabase/migrations/20260307_create_validation_runs.sql) to store validation history in `validation_runs`. The `playbooks.payload.validation` field remains a cached latest summary, but validation events now belong in a dedicated table. Locator sets are versioned by route fingerprint, and a validation fingerprint mismatch will mark the active playbook as `stale` so it stops serving automatically.

Apply [`supabase/migrations/20260307_create_benchmark_runs.sql`](/Users/bhanuprasadthota/Desktop/AgentAtlas/supabase/migrations/20260307_create_benchmark_runs.sql) to persist benchmark suite history in `benchmark_runs`.

## Integration benchmarks

[`test_execute.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/test_execute.py) is now an opt-in integration harness for warm-start reliability, not a top-level demo script. It benchmarks repeated `get_schema()` calls plus `validate()` across public workflows and reports:

- first lookup source and token use
- second lookup warm-start registry hit behavior
- locator count
- validation status
- fingerprint match and schema version
- workflow category so regressions can be grouped by auth walls, delayed hydration, repeated labels, and dynamic forms

Run the benchmark suite directly:

```bash
AGENTATLAS_RUN_INTEGRATION=1 python3 test_execute.py
```

Run it through `pytest` only when you want live integration coverage:

```bash
AGENTATLAS_RUN_INTEGRATION=1 pytest -q test_execute.py
```

Optional:

- set `AGENTATLAS_BENCHMARK_OUTPUT=/path/to/results.json` to persist the benchmark output
- benchmark output now includes `validation_message` and `failed_locators` so degraded runs are actionable
- validation uses automatic relearning on `degraded` and `stale` results before returning the final benchmark status
- successful runs are also stored in Supabase `benchmark_runs` when the table exists
- compare the latest two runs with `python3 compare_benchmark_runs.py`; exit code `2` indicates a regression

## Scheduled revalidation

Use the registry revalidation cycle to refresh stale, degraded, or aged playbooks before customers hit them:

```bash
python3 run_revalidation_cycle.py
```

Optional environment variables:

- `AGENTATLAS_REVALIDATION_MAX_AGE_HOURS`
- `AGENTATLAS_REVALIDATION_LIMIT`
- `AGENTATLAS_REVALIDATION_HEADLESS`

## Hosted API

The first hosted API surface now exists in [`agentatlas/api.py`](/Users/bhanuprasadthota/Desktop/AgentAtlas/agentatlas/api.py:1). Core endpoints:

- `GET /admin`
- `GET /health`
- `POST /v1/schema/resolve`
- `POST /v1/locator/resolve`
- `POST /v1/validate`
- `POST /v1/outcome`
- `GET /v1/benchmarks/runs`
- `GET /v1/benchmarks/compare`
- `GET /v1/benchmarks/dashboard`
- `GET /v1/review/queue`
- `GET /v1/review/audit`
- `POST /v1/review/promote`
- `POST /v1/review/diff`

Run it locally with:

```bash
uvicorn agentatlas.api:app --reload
```

Protect the hosted API with an API key by setting either:

```bash
AGENTATLAS_API_KEY=single-shared-key
```

or:

```bash
AGENTATLAS_API_KEYS=key-one,key-two,key-three
```

For tenant-scoped keys, prefer:

```bash
AGENTATLAS_TENANT_API_KEYS=tenant-a:key-a|key-a-2;tenant-b:key-b
```

In that mode, clients must send both `X-Tenant-ID` and `X-API-Key`.

Then call protected endpoints with:

```bash
curl -X POST http://127.0.0.1:8000/v1/schema/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: single-shared-key" \
  -d '{"site":"httpbin.org","url":"https://httpbin.org/forms/post"}'
```

Use the hosted client mode in Python with:

```python
from agentatlas import Atlas

atlas = Atlas(
    api_url="https://your-agentatlas-api.example.com",
    api_key="single-shared-key",
    tenant_id="tenant-a",
    use_api=True,
    registry_scope="auto",
)

schema = await atlas.get_schema(
    site="httpbin.org",
    url="https://httpbin.org/forms/post",
)

report = await atlas.validate(
    site="httpbin.org",
    url="https://httpbin.org/forms/post",
)

locator = await atlas.resolve_locator(
    site="httpbin.org",
    url="https://httpbin.org/forms/post",
    element_name="customer_name",
)

recorded = await atlas.record_outcome(
    site="httpbin.org",
    url="https://httpbin.org/forms/post",
    status="success",
)
```

Direct-mode review operations:

```python
queue = await atlas.list_review_queue(limit=20)
await atlas.promote_playbook(playbook_id="...", reviewer="ops@agentatlas.ai", approved=True, notes="Verified selectors")
```

Tenant-scoped benchmark dashboards are available through the API and registry history layer. Benchmark runs now persist tenant metadata so reliability trends can be viewed per tenant instead of only globally.

The lightweight admin UI is served from `/admin` and uses the same authenticated API surface for:

- benchmark dashboard by tenant
- review queue management
- audit trail visibility
- private/public route diff inspection

## Deployment

The repo now includes:

- [`Dockerfile`](/Users/bhanuprasadthota/Desktop/AgentAtlas/Dockerfile) for container deployment
- [`render.yaml`](/Users/bhanuprasadthota/Desktop/AgentAtlas/render.yaml) for quick Render deployment
- [`fly.toml`](/Users/bhanuprasadthota/Desktop/AgentAtlas/fly.toml) for Fly.io deployment
- [`railway.json`](/Users/bhanuprasadthota/Desktop/AgentAtlas/railway.json) for Railway deployment
- [`deploy/ecs-task-definition.json`](/Users/bhanuprasadthota/Desktop/AgentAtlas/deploy/ecs-task-definition.json) as an ECS/Fargate starting point

Minimal centralized deployment flow:

```bash
docker build -t agentatlas-api .
docker run -p 8000:8000 \
  -e SUPABASE_URL=your_supabase_url \
  -e SUPABASE_SERVICE_ROLE_KEY=your_key \
  -e OPENAI_API_KEY=your_key \
  -e AGENTATLAS_API_KEY=single-shared-key \
  agentatlas-api
```

For a real central service, deploy the container to Render, Railway, Fly.io, ECS, or another container platform and point SDK users at the shared base URL via `AGENTATLAS_API_URL`.

Platform notes:

- Render: `render.yaml` plus env vars in the dashboard; custom domains and TLS are managed by Render
- Fly.io: `fly launch --copy-config --ha=false`, then `fly secrets set ...`; TLS is automatic on the Fly hostname and custom domains can be added with certificates
- Railway: connect the repo or Docker image, set env vars, and Railway terminates TLS on the assigned or custom domain
- ECS/Fargate: use the task definition with an ALB in front, then attach ACM certificates to the HTTPS listener for TLS

## Operations

Backfill fingerprints for legacy active playbooks that were learned before fingerprint versioning was introduced:

```bash
python3 backfill_fingerprints.py
```

Optional:

- set `AGENTATLAS_BACKFILL_LIMIT=250` to control batch size

## Strategic value

AgentAtlas is intended to become shared infrastructure for web automation systems:

- Less repeated LLM perception across the same sites
- Faster warm-start browser tasks
- Reusable locator memory across users, agents, and teams
- A growing validation graph for freshness and trust

## License

MIT
