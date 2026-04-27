# Benchmark Methodology

AgentAtlas measures one thing: **how much repeated LLM perception it eliminates**.

The core claim is that a second `get_schema()` call on a known page should cost **0 tokens** and return from the registry in milliseconds. The benchmark suite verifies this claim across 13 real public URLs covering 7 page categories.

## What "warm registry hit" means

A workflow passes if and only if:
- `second_schema.source == "registry"` — the second call was served from memory, not the LLM
- `second_schema.tokens_used == 0` — no tokens were consumed on the warm path
- `validation.status in {"healthy", "degraded"}` — locators still work on the live page

A workflow fails if the second call still goes to the LLM, or if validation reports all locators broken.

## Running the suite

```bash
# Prerequisites
pip install agentatlas playwright
playwright install chromium

# Set credentials
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...
export OPENAI_API_KEY=...

# Run
AGENTATLAS_RUN_INTEGRATION=1 python3 test_execute.py

# Save output
AGENTATLAS_RUN_INTEGRATION=1 AGENTATLAS_BENCHMARK_OUTPUT=benchmarks/latest.json python3 test_execute.py

# Check for regressions vs previous run
python3 compare_benchmark_runs.py
```

## Workflow categories

| Category | What it tests |
|----------|--------------|
| `minimal_static` | Pure server-rendered HTML, zero JS — baseline for registry reliability |
| `content_page` | Long-form content with stable heading/section structure (Wikipedia, arXiv, PyPI) |
| `repeated_labels` | Pages with many similar elements that could cause ambiguity (HN, books.toscrape.com) |
| `dynamic_form` | Forms with multiple input types and submit buttons |
| `auth_wall` | Public login pages — stable structure, high reuse value |
| `delayed_hydration` | JS-rendered pages where DOM settles after initial load |
| `job_board` | ATS platforms (Greenhouse, Lever) — highest real-world reuse frequency |

## Expected results

On a clean run against a registry with all 13 workflows already learned:

| Metric | Expected |
|--------|----------|
| Warm registry hit rate | 100% |
| Healthy validation rate | ≥ 85% (some pages may show degraded locators due to A/B tests) |
| Second-run tokens | 0 for all workflows |
| Cold start (first run) | 800–2,400 tokens per page depending on DOM complexity |
| Warm start latency | 50–500ms (registry lookup + network) |
| Cold start latency | 8–30s (LLM + browser) |

**Token savings per warm hit** (vs re-running LLM perception every time):

| Page type | Cold tokens | Warm tokens | Saving |
|-----------|-------------|-------------|--------|
| Minimal static | ~800 | 0 | 100% |
| Content page | ~1,800 | 0 | 100% |
| Auth wall | ~1,200 | 0 | 100% |
| JS-rendered | ~2,400 | 0 | 100% |
| Job board | ~1,600 | 0 | 100% |

For a pipeline hitting 10 job boards daily, that is ~16,000 tokens/day saved per board — roughly **$0.24/day at GPT-4o pricing**, or **~$1,500/year per board** at scale.

## Benchmark CI

The benchmark suite runs every Monday via GitHub Actions and commits results to `benchmarks/`. See [`.github/workflows/benchmark.yml`](.github/workflows/benchmark.yml).

Regressions (warm hit lost, validation degraded) are flagged and the workflow exits with code `2`. Historical runs are stored in the `benchmark_runs` Supabase table and visible in the `/admin` dashboard.

## Adding a workflow

```python
# In test_execute.py — add to BENCHMARK_WORKFLOWS:
BenchmarkWorkflow(
    name="my_site",
    site="example.com",
    url="https://example.com/some/page",
    notes="What this page tests and why.",
    category="content_page",  # one of the categories above
)
```

The new workflow will be picked up by the next scheduled run automatically.
