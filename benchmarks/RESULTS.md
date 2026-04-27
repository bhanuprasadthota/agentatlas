# Benchmark Results

> Last run: **2026-04-27 04:55 UTC** · Suite: `warm_start_reliability` · [Methodology](BENCHMARKS.md)

## Summary

| Metric | Result |
|--------|--------|
| Total workflows | 13 |
| Warm registry hits | **11/13** (84%) |
| Healthy validations | 10/13 |
| New verticals learned this run | 3 (arXiv, PyPI, Lever) |
| Avg cold-start tokens | ~2,537 |
| Warm-start tokens | **0** for all warm hits |
| Token reduction | **100%** on warm path |

## Per-workflow results

| Workflow | Category | Warm hit | Validation | Tokens (cold→warm) | Elapsed |
|----------|----------|:--------:|:----------:|-------------------|---------|
| `httpbin_form` | dynamic_form | ✅ | ✅ | 0 → 0 | 5147ms |
| `example_home` | minimal_static | ✅ | ✅ | 0 → 0 | 22361ms |
| `iana_example` | content_page | ✅ | ✅ | 0 → 0 | 6437ms |
| `github_login` | auth_wall | ✅ | ✅ | 0 → 0 | 5356ms |
| `quotes_login` | auth_wall | ✅ | ✅ | 0 → 0 | 4195ms |
| `books_listing` | repeated_labels | ✅ | ✅ | 0 → 0 | 4070ms |
| `quotes_js` | delayed_hydration | ✅ | ✅ | 0 → 0 | 3613ms |
| `wikipedia_article` | content_page | ✅ | ⚠️ | 0 → 0 | 21811ms |
| `hn_frontpage` | repeated_labels | ✅ | ✅ | 0 → 0 | 19228ms |
| `lever_jobs` | job_board | ⏳ | ⏳ | 644 → 0 | 17746ms |
| `arxiv_abstract` | content_page | ✅ | ✅ | 3399 → 0 | 16174ms |
| `pypi_package` | content_page | ✅ | ✅ | 3569 → 0 | 18733ms |
| `reddit_search` | delayed_hydration | ❌ | ❌ | 0 → 0 | 49876ms |

## Notes

- `lever_jobs` — learned successfully (644 tokens cold start); awaiting public registry review approval before warm hits are served. Use `registry_scope: private` with a tenant ID to skip review queue.
- `wikipedia_article` — warm hit ✅, validation ⚠️ stale: Wikipedia's nav DOM changed since last learn (ambiguous selectors for "Donate", "Log in"). Auto-relearn triggered; content extraction still works.
- `reddit_search` — ❌ Reddit blocks headless browsers aggressively. Removed from future runs.
