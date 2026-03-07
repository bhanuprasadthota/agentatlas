# Changelog

## 0.3.1

### Stable SDK surface
- `Atlas.get_schema`
- `Atlas.get_playbook`
- `Atlas.resolve_locator`
- `Atlas.validate`
- `Atlas.record_outcome`
- `Atlas.list_review_queue`
- `Atlas.list_review_audit`
- `Atlas.promote_playbook`
- `Atlas.get_route_scope_diff`
- `AgentExecutor.execute`

### Experimental SDK surface
- `Atlas.run_revalidation_cycle`
- `Atlas.list_revalidation_candidates`

### Deprecations
- `Atlas.execute()` is no longer part of the stable Atlas surface.
  Use `AgentExecutor.execute()` instead.

### Notes
- Compatibility contract applies to the stable SDK surface only.
- Experimental methods may change before the next minor release.

## v0.4.0

### Hardening
- Coordinated stale recovery — single-writer lease, explicit recovery states
- Cold start timeout contract — timeout_seconds param, progress callbacks
- SPA stabilization — bounded DOM settle pass before learn/validate/crawl
- Supabase circuit breaker — warm hits survive registry outages, in-memory cache
- SDK versioning contract — stable/experimental surface, deprecation warnings

### Trust & Ops
- Review SLA tracking — queue age, overdue flags, dashboard metrics
- Abuse reporting — flag_schema() from SDK and API, auto-re-queues public schemas
- Durable audit storage — review_events table, falls back to payload history
- pending_review caller state — deterministic response for queued public schemas

### Examples
- extract_product_cards.py — second deterministic extraction vertical
- extract_job_listings_hosted_api.py — hosted API extraction path
