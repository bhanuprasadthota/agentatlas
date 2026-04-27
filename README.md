# AgentAtlas

[![Tests](https://github.com/bhanuprasadthota/agentatlas/actions/workflows/test.yml/badge.svg)](https://github.com/bhanuprasadthota/agentatlas/actions/workflows/test.yml)
[![Benchmarks](https://github.com/bhanuprasadthota/agentatlas/actions/workflows/benchmark.yml/badge.svg)](https://github.com/bhanuprasadthota/agentatlas/actions/workflows/benchmark.yml)
[![PyPI](https://img.shields.io/pypi/v/agentatlas)](https://pypi.org/project/agentatlas/)
[![Python](https://img.shields.io/pypi/pyversions/agentatlas)](https://pypi.org/project/agentatlas/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Shared web interaction memory for AI agents. Learn a page once, reuse it everywhere at 0 tokens.**

AgentAtlas is the registry layer for browser agents. It learns stable page locators once, stores them as reusable schemas, and validates them over time — so any agent can reuse that memory instead of paying repeated LLM perception costs on the same pages.

```
Cold page → LLM learns locators → saved to registry (once)
Warm page → registry hit → 0 tokens, milliseconds ✓
```

---

## Why this matters

Every browser agent re-perceives the same pages on every run. A pipeline that hits 10 job boards daily burns ~16,000 LLM tokens/day just on repeated DOM understanding — work that was already done the run before.

AgentAtlas eliminates that. The second run is always free.

---

## Benchmark results

13 workflows across 7 page categories — [full results](benchmarks/RESULTS.md) · [methodology](BENCHMARKS.md)

| Metric | Result |
|--------|--------|
| Warm registry hits | **11/13 (84%)** — 2 pending review queue |
| Warm-start tokens | **0** for all warm hits |
| Cold-start tokens | 644–3,569 depending on page complexity |
| Token reduction | **100%** on warm path |
| Warm start latency | 3–22s (browser validation included) |

**Per-workflow (latest run 2026-04-27):**

| Workflow | Category | Warm hit | Cold tokens |
|----------|----------|:--------:|-------------|
| `httpbin_form` | dynamic_form | ✅ | already known |
| `github_login` | auth_wall | ✅ | already known |
| `books_listing` | repeated_labels | ✅ | already known |
| `quotes_js` | delayed_hydration | ✅ | already known |
| `hn_frontpage` | repeated_labels | ✅ | already known |
| `wikipedia_article` | content_page | ✅ | already known |
| `arxiv_abstract` | content_page | ✅ | 3,399 (first run) |
| `pypi_package` | content_page | ✅ | 3,569 (first run) |
| `lever_jobs` | job_board | ⏳ review | 644 (first run) |

See [benchmarks/RESULTS.md](benchmarks/RESULTS.md) for the full table with validation status and elapsed times.

---

## Install

```bash
pip install agentatlas
playwright install chromium
```

---

## Quick start (5 minutes to first warm hit)

```bash
# 1. Set credentials
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
export OPENAI_API_KEY=your-openai-key

# 2. Run any extraction demo — first run learns the page, second run is 0 tokens
python3 examples/extract_job_listings.py       # Greenhouse job boards
python3 examples/extract_product_cards.py      # E-commerce product listings
python3 examples/extract_hn_posts.py           # Hacker News front page
python3 examples/extract_wikipedia.py          # Wikipedia articles
python3 examples/extract_lever_jobs.py         # Lever job boards
```

First run output:
```text
Step 1 - loading UI anchors from registry...
  source      : llm_learned        ← page learned, costs tokens once
  tokens_used : 1842
  elapsed     : 12300ms

Step 2 - extracting job listings from page...
  Jobs extracted : 24
```

Second run (same command):
```text
Step 1 - loading UI anchors from registry...
  source      : registry           ← served from memory
  tokens_used : 0                  ← 0 tokens
  elapsed     : 312ms              ← milliseconds, not seconds
```

---

## Core API

```python
from agentatlas import Atlas

atlas = Atlas()

# Get or learn a page schema
schema = await atlas.get_schema(
    site="boards.greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
)
print(schema.source)       # "registry" or "llm_learned"
print(schema.tokens_used)  # 0 on warm hits

# Validate that stored locators still work on the live page
report = await atlas.validate(
    site="boards.greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
)
print(report.status)  # "healthy" | "degraded" | "stale" | "failed"

# Resolve a single element locator
locator = await atlas.resolve_locator(
    site="boards.greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
    element_name="job_title",
)

# Record execution outcome for telemetry
await atlas.record_outcome(
    site="boards.greenhouse.io",
    url="https://boards.greenhouse.io/anthropic",
    status="success",
)
```

---

## Supported verticals

| Vertical | Example | Category |
|----------|---------|----------|
| Job boards (Greenhouse) | `extract_job_listings.py` | `job_board` |
| Job boards (Lever) | `extract_lever_jobs.py` | `job_board` |
| E-commerce catalogs | `extract_product_cards.py` | `repeated_labels` |
| News / encyclopedia | `extract_wikipedia.py` | `content_page` |
| Link aggregators | `extract_hn_posts.py` | `minimal_static` |
| Auth walls | benchmark workflow | `auth_wall` |
| JS-rendered pages | benchmark workflow | `delayed_hydration` |

---

## Hosted API

Run AgentAtlas as a shared service so multiple agents and teams can hit the same registry without each needing Supabase credentials.

**Live API: `https://agentatlas-8qp0.onrender.com`**

**Deploy your own in 10 minutes → [deploy/render-setup.md](deploy/render-setup.md)**

Quick test against the live API:
```bash
curl -s -X POST https://agentatlas-8qp0.onrender.com/v1/schema/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"site":"example.com","url":"https://example.com/","registry_scope":"auto"}'
# Returns: {"schema": {"source": "registry", "tokens_used": 0, ...}}
```

Or in Python:
```python
atlas = Atlas(
    api_url="https://agentatlas-8qp0.onrender.com",
    api_key="your-key",
    tenant_id="my-team",
    use_api=True,
    registry_scope="auto",
)
schema = await atlas.get_schema(site="example.com", url="https://example.com/")
print(schema.source)       # "registry"
print(schema.tokens_used)  # 0
```

API endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `POST /v1/schema/resolve` | Get or learn a page schema |
| `POST /v1/locator/resolve` | Resolve a single element |
| `POST /v1/validate` | Validate locator health |
| `POST /v1/outcome` | Record execution telemetry |
| `GET /v1/benchmarks/dashboard` | Benchmark trends by category |
| `GET /v1/review/queue` | Review queue for public schemas |
| `GET /admin` | Admin UI |

---

## Environment variables

```bash
# Required (direct mode)
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_key
OPENAI_API_KEY=your_key

# Required (hosted API mode)
AGENTATLAS_API_URL=https://your-api.onrender.com
AGENTATLAS_API_KEY=your_key

# Optional
AGENTATLAS_TENANT_ID=my-tenant
AGENTATLAS_REGISTRY_SCOPE=auto          # auto | public | private
AGENTATLAS_DEVICE_CLASS=desktop         # variant inference
AGENTATLAS_LOCALE=enUS
AGENTATLAS_AUTH_STATE=anonymous
AGENTATLAS_REGION=us
AGENTATLAS_DOMAIN_CLASS_POLICIES=job_board:review_required;docs:auto_approve
```

---

## Database setup

Apply the combined migration to your Supabase project (SQL Editor → paste → run):

```bash
# All three tables in one file
supabase/setup.sql
```

Or apply individually:
```
supabase/migrations/20260307_create_validation_runs.sql
supabase/migrations/20260307_create_benchmark_runs.sql
supabase/migrations/20260307_create_review_events.sql
```

---

## Registry scopes

| Scope | Behavior |
|-------|----------|
| `public` | Shared memory across all tenants |
| `private` | Isolated to your tenant |
| `auto` | Private-first, public-fallback (default) |

High-value public domains (job boards, social auth) require review before becoming serveable. Use `registry_scope="private"` to skip the review queue during development.

---

## Module layout

| Module | Purpose |
|--------|---------|
| `agentatlas/atlas.py` | Main facade — direct and hosted API dispatch |
| `agentatlas/client.py` | Hosted API HTTP client with retries |
| `agentatlas/browser_runtime.py` | Playwright learning, validation, execution |
| `agentatlas/registry.py` | Playbook persistence and route lookup |
| `agentatlas/registry_quality.py` | Trust scoring, scope conflict resolution |
| `agentatlas/registry_review.py` | Review queue, audit trail, promotion |
| `agentatlas/registry_benchmarks.py` | Benchmark history and revalidation |
| `agentatlas/api.py` | FastAPI hosted API server |

---

## Running benchmarks

```bash
AGENTATLAS_RUN_INTEGRATION=1 python3 test_execute.py

# Compare latest two runs (exit code 2 = regression)
python3 compare_benchmark_runs.py
```

Benchmarks run automatically every Monday via [GitHub Actions](.github/workflows/benchmark.yml) and results are committed to `benchmarks/`. See [BENCHMARKS.md](BENCHMARKS.md) for the full methodology.

---

## Operations

```bash
# Refresh stale or degraded playbooks proactively
python3 run_revalidation_cycle.py

# Backfill fingerprints for pre-fingerprinting playbooks
python3 backfill_fingerprints.py
```

---

## Strategic value

AgentAtlas is intended to become shared infrastructure for web automation:

- **Less repeated LLM perception** across the same sites and routes
- **Faster warm-start browser tasks** — registry hits in milliseconds, not seconds
- **Shared memory across agents and teams** — one team's cold start benefits everyone
- **Growing validation graph** for freshness, trust, and drift detection

---

## License

MIT
