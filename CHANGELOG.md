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
