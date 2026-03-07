from dataclasses import dataclass, field


@dataclass
class SiteSchema:
    site: str
    url: str
    route_key: str
    status: str
    confidence: float
    elements: dict
    source: str
    tokens_used: int
    message: str
    recovery_state: str | None = None


@dataclass
class ExecuteResult:
    site: str
    task: str
    status: str
    steps_taken: int
    total_tokens: int
    data: dict
    history: list


@dataclass
class LocatorResolution:
    element: str
    selector_type: str
    selector: str
    matched: bool
    visible: bool
    match_count: int
    actionable: bool = False
    ambiguous: bool = False
    error: str = ""


@dataclass
class ValidationReport:
    site: str
    url: str
    route_key: str
    status: str
    source: str
    validation_count: int
    success_count: int
    failure_count: int
    success_rate: float
    last_validated_at: str
    schema_version: int | None = None
    stored_fingerprint: str | None = None
    current_fingerprint: str | None = None
    fingerprint_match: bool | None = None
    locator_results: list[LocatorResolution] = field(default_factory=list)
    message: str = ""
    recovery_state: str | None = None


@dataclass
class PlaybookRecord:
    site: str
    url: str
    route_key: str
    task_key: str
    variant_key: str
    confidence: float
    elements: dict
    source: str
    schema_version: int = 1
    fingerprint: str | None = None
    last_validated_at: str | None = None
    success_rate: float | None = None
    validation_count: int = 0
    trust_score: float | None = None
    quality_status: str = "candidate"
    serveable: bool = True
    registry_scope: str = "public"
    tenant_id: str | None = None
    review_status: str = "approved"
    metadata: dict = field(default_factory=dict)


@dataclass
class ResolveSchemaResponse:
    schema: SiteSchema
    playbook: PlaybookRecord | None = None


@dataclass
class ResolveLocatorResponse:
    element_name: str
    locator: dict | None
    playbook: PlaybookRecord | None = None


@dataclass
class ReviewQueueItem:
    playbook_id: str
    site: str | None = None
    url: str | None = None
    route_key: str | None = None
    variant_key: str | None = None
    confidence: float | None = None
    review_status: str = "review_required"
    review_reason: str | None = None
    registry_scope: str = "public"
    tenant_id: str | None = None


@dataclass
class ReviewAuditEvent:
    playbook_id: str
    timestamp: str
    reviewer: str
    reviewer_role: str
    action: str
    notes: str = ""
    site: str | None = None
    url: str | None = None
    route_key: str | None = None
    variant_key: str | None = None
    tenant_id: str | None = None
    registry_scope: str = "public"


@dataclass
class RouteScopeDiff:
    site: str
    url: str
    task_key: str
    variant_key: str
    tenant_id: str | None
    decision: dict
    private: dict | None
    public: dict | None
    route_differences: list[dict] = field(default_factory=list)
