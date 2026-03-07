"""Versioning and compatibility contract for the AgentAtlas SDK."""

from __future__ import annotations

import warnings

SDK_VERSION = "0.3.1"
API_VERSION = "v1"

STABLE_SURFACE = {
    "Atlas.get_schema",
    "Atlas.get_playbook",
    "Atlas.resolve_locator",
    "Atlas.validate",
    "Atlas.record_outcome",
    "Atlas.list_review_queue",
    "Atlas.list_review_audit",
    "Atlas.promote_playbook",
    "Atlas.get_route_scope_diff",
    "AgentExecutor.execute",
}

EXPERIMENTAL_SURFACE = {
    "Atlas.run_revalidation_cycle",
    "Atlas.list_revalidation_candidates",
}


def warn_deprecated(message: str, *, stacklevel: int = 2) -> None:
    warnings.warn(message, DeprecationWarning, stacklevel=stacklevel)
