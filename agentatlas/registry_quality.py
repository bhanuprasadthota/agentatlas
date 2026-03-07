import os
import re

from agentatlas.registry_common import DEFAULT_VARIANT_KEY, VALID_ROLE_NAMES


class AtlasQualityMixin:
    @staticmethod
    def _compute_quality_summary(
        confidence: float,
        validation: dict | None,
        telemetry: dict | None,
        promotion: dict | None = None,
        registry: dict | None = None,
    ) -> dict:
        validation = validation or {}
        telemetry = telemetry or {}
        promotion = promotion or {}
        registry = registry or {}
        confidence_value = float(confidence or 0.0)
        validation_count = int(validation.get("validation_count") or 0)
        success_rate = validation.get("success_rate")
        success_rate_value = success_rate if success_rate is not None else 0.5
        validation_status = (validation.get("status") or "").strip().lower()
        outcomes = telemetry.get("outcomes", [])[-20:]
        successful_outcomes = sum(
            1 for item in outcomes if (item.get("status") or "").strip().lower() in {"success", "healthy", "validated", "completed"}
        )
        failed_outcomes = sum(
            1 for item in outcomes if (item.get("status") or "").strip().lower() in {"failed", "error", "timeout", "stale"}
        )
        total_outcomes = successful_outcomes + failed_outcomes
        outcome_success_rate = (successful_outcomes / total_outcomes) if total_outcomes else 0.5
        trust_score = round(
            max(0.0, min(confidence_value, 1.0)) * 0.25
            + max(0.0, min(success_rate_value, 1.0)) * 0.45
            + min(validation_count / 5, 1.0) * 0.20
            + outcome_success_rate * 0.10,
            3,
        )
        review_status = promotion.get("review_status", "approved")
        review_required = review_status == "review_required"
        rejected = review_status == "rejected"
        is_public = registry.get("scope", "public") == "public"
        if rejected:
            quality_status = "quarantined"
            serveable = False
        elif review_required and is_public:
            quality_status = "candidate"
            serveable = False
        elif validation_status in {"stale", "failed"}:
            quality_status = "quarantined"
            serveable = False
        elif validation_count >= 2 and success_rate is not None and success_rate < 0.6:
            quality_status = "quarantined"
            serveable = False
        elif failed_outcomes >= 3 and outcome_success_rate < 0.5:
            quality_status = "quarantined"
            serveable = False
        elif validation_count >= 3 and success_rate is not None and success_rate >= 0.95:
            quality_status = "trusted"
            serveable = True
        elif validation_count >= 1 and success_rate is not None and success_rate >= 0.8:
            quality_status = "verified"
            serveable = True
        else:
            quality_status = "candidate"
            serveable = True
        return {
            "quality_status": quality_status,
            "serveable": serveable,
            "trust_score": trust_score,
            "signals": {
                "validation_count": validation_count,
                "success_rate": success_rate,
                "validation_status": validation_status or None,
                "successful_outcomes": successful_outcomes,
                "failed_outcomes": failed_outcomes,
                "outcome_success_rate": outcome_success_rate if total_outcomes else None,
                "review_status": review_status,
            },
        }

    @staticmethod
    def _filter_playbooks_by_scope(playbooks: list[dict], tenant_id: str | None, registry_scope: str) -> list[dict]:
        if not playbooks:
            return []
        private_rows = []
        public_rows = []
        for row in playbooks:
            payload = row.get("payload") or {}
            registry = payload.get("registry") or {}
            scope = registry.get("scope", "public")
            row_tenant_id = registry.get("tenant_id")
            if scope == "private" and tenant_id and row_tenant_id == tenant_id:
                private_rows.append(row)
            elif scope != "private":
                public_rows.append(row)
        if registry_scope == "private":
            return private_rows
        if registry_scope == "public":
            return public_rows
        return private_rows + public_rows

    @staticmethod
    def _rank_playbooks_for_variant(playbooks: list[dict], variant_key: str) -> list[dict]:
        def variant_rank(row: dict) -> tuple[int, float, int]:
            candidate_variant = row.get("variant_key") or DEFAULT_VARIANT_KEY
            exact = 0 if candidate_variant == variant_key else 1
            confidence = -(row.get("confidence") or 0.0)
            version = -(row.get("version") or 0)
            return (exact, confidence, version)

        return sorted(playbooks, key=variant_rank)

    @staticmethod
    def _build_promotion_state(site: str, registry_scope: str, tenant_id: str | None) -> dict:
        domain_class = AtlasQualityMixin._classify_domain(site)
        policy = AtlasQualityMixin._approval_policy_for_domain_class(domain_class)
        if registry_scope == "public" and policy == "review_required":
            return {
                "review_status": "review_required",
                "review_reason": f"domain_class:{domain_class}",
                "reviewed_by": None,
                "reviewed_at": None,
                "review_notes": "",
                "domain_class": domain_class,
                "tenant_id": tenant_id,
            }
        return {
            "review_status": "approved",
            "review_reason": f"domain_class:{domain_class}",
            "reviewed_by": "system",
            "reviewed_at": None,
            "review_notes": "",
            "domain_class": domain_class,
            "tenant_id": tenant_id,
        }

    @staticmethod
    def _load_domain_class_policies() -> dict[str, str]:
        raw = os.getenv(
            "AGENTATLAS_DOMAIN_CLASS_POLICIES",
            "social_auth:review_required;job_board:review_required;commerce:review_required;docs:auto_approve;general:auto_approve",
        ).strip()
        policies: dict[str, str] = {}
        for entry in raw.split(";"):
            if not entry.strip():
                continue
            domain_class, separator, policy = entry.partition(":")
            if not separator:
                continue
            policies[domain_class.strip()] = policy.strip()
        return policies

    @staticmethod
    def _approval_policy_for_domain_class(domain_class: str) -> str:
        return AtlasQualityMixin._load_domain_class_policies().get(domain_class, "auto_approve")

    @staticmethod
    def _classify_domain(site: str) -> str:
        raw = (site or "").strip().lower()
        classes = {
            "job_board": {"greenhouse.io", "lever.co", "workday.com", "ashbyhq.com", "indeed.com"},
            "social_auth": {"github.com", "linkedin.com", "google.com", "facebook.com"},
            "commerce": {"amazon.com", "shopify.com", "walmart.com", "ebay.com"},
            "docs": {"iana.org", "example.com", "readthedocs.io", "developer.mozilla.org"},
        }
        for domain_class, domains in classes.items():
            if any(raw == domain or raw.endswith(f".{domain}") for domain in domains):
                return domain_class
        return "general"

    def _resolve_scope_conflicts(
        self,
        playbooks: list[dict],
        variant_key: str,
        tenant_id: str | None,
        registry_scope: str,
    ) -> list[dict]:
        if registry_scope in {"public", "private"}:
            return self._rank_playbooks_for_variant(playbooks, variant_key=variant_key)

        private_rows = self._rank_playbooks_for_variant(
            self._filter_playbooks_by_scope(playbooks, tenant_id=tenant_id, registry_scope="private"),
            variant_key=variant_key,
        )
        public_rows = self._rank_playbooks_for_variant(
            self._filter_playbooks_by_scope(playbooks, tenant_id=tenant_id, registry_scope="public"),
            variant_key=variant_key,
        )
        if not private_rows:
            return public_rows
        if not public_rows:
            return private_rows

        private_best = private_rows[0]
        public_best = public_rows[0]
        private_quality = self._compute_quality_summary(
            confidence=private_best.get("confidence", 0.0),
            validation=(private_best.get("payload") or {}).get("validation", {}),
            telemetry=(private_best.get("payload") or {}).get("telemetry", {}),
            promotion=(private_best.get("payload") or {}).get("promotion", {}),
            registry=(private_best.get("payload") or {}).get("registry", {}),
        )
        public_quality = self._compute_quality_summary(
            confidence=public_best.get("confidence", 0.0),
            validation=(public_best.get("payload") or {}).get("validation", {}),
            telemetry=(public_best.get("payload") or {}).get("telemetry", {}),
            promotion=(public_best.get("payload") or {}).get("promotion", {}),
            registry=(public_best.get("payload") or {}).get("registry", {}),
        )
        private_fingerprint = self._fingerprint_value(private_best.get("payload"))
        public_fingerprint = self._fingerprint_value(public_best.get("payload"))

        if private_quality.get("serveable") and not public_quality.get("serveable"):
            return private_rows + public_rows
        if public_quality.get("serveable") and not private_quality.get("serveable"):
            return public_rows + private_rows
        if private_fingerprint and public_fingerprint and private_fingerprint != public_fingerprint:
            if (
                public_quality.get("quality_status") in {"trusted", "verified"}
                and private_quality.get("quality_status") == "candidate"
                and (public_quality.get("trust_score") or 0.0) >= (private_quality.get("trust_score") or 0.0) + 0.1
            ):
                return public_rows + private_rows
        return private_rows + public_rows

    @staticmethod
    def _get_reviewer_role(reviewer: str) -> str:
        raw = os.getenv("AGENTATLAS_REVIEWER_ROLES", "").strip()
        if not raw:
            return "admin"
        mapping = {}
        for entry in raw.split(";"):
            if not entry.strip():
                continue
            subject, separator, role = entry.partition(":")
            if separator and subject.strip() and role.strip():
                mapping[subject.strip().lower()] = role.strip().lower()
        return mapping.get((reviewer or "").strip().lower(), "viewer")

    def _playbook_diff_summary(self, row: dict | None) -> dict | None:
        if not row:
            return None
        payload = row.get("payload") or {}
        quality = self._compute_quality_summary(
            confidence=row.get("confidence", 0.0),
            validation=payload.get("validation", {}),
            telemetry=payload.get("telemetry", {}),
            promotion=payload.get("promotion", {}),
            registry=payload.get("registry", {}),
        )
        return {
            "playbook_id": row.get("id"),
            "variant_key": row.get("variant_key"),
            "version": row.get("version"),
            "scope": (payload.get("registry") or {}).get("scope", "public"),
            "tenant_id": (payload.get("registry") or {}).get("tenant_id"),
            "fingerprint": self._fingerprint_value(payload),
            "quality_status": quality.get("quality_status"),
            "trust_score": quality.get("trust_score"),
            "serveable": quality.get("serveable"),
            "review_status": (payload.get("promotion") or {}).get("review_status"),
            "elements": self.build_elements(payload),
        }

    def _diff_locator_sets(self, private_row: dict | None, public_row: dict | None) -> list[dict]:
        private_elements = self.build_elements((private_row or {}).get("payload") or {})
        public_elements = self.build_elements((public_row or {}).get("payload") or {})
        keys = sorted(set(private_elements.keys()) | set(public_elements.keys()))
        diffs = []
        for key in keys:
            left = private_elements.get(key)
            right = public_elements.get(key)
            if left == right:
                continue
            diffs.append({"element": key, "private": left, "public": right})
        return diffs

    def _explain_scope_conflict(
        self,
        private_row: dict | None,
        public_row: dict | None,
        resolved_row: dict | None,
    ) -> dict:
        if not private_row and public_row:
            return {"winner": "public", "reason": "no_private_playbook"}
        if not public_row and private_row:
            return {"winner": "private", "reason": "no_public_playbook"}
        if not resolved_row:
            return {"winner": None, "reason": "no_resolved_playbook"}
        resolved_scope = ((resolved_row.get("payload") or {}).get("registry") or {}).get("scope", "public")
        private_quality = self._compute_quality_summary(
            confidence=(private_row or {}).get("confidence", 0.0),
            validation=((private_row or {}).get("payload") or {}).get("validation", {}),
            telemetry=((private_row or {}).get("payload") or {}).get("telemetry", {}),
            promotion=((private_row or {}).get("payload") or {}).get("promotion", {}),
            registry=((private_row or {}).get("payload") or {}).get("registry", {}),
        ) if private_row else None
        public_quality = self._compute_quality_summary(
            confidence=(public_row or {}).get("confidence", 0.0),
            validation=((public_row or {}).get("payload") or {}).get("validation", {}),
            telemetry=((public_row or {}).get("payload") or {}).get("telemetry", {}),
            promotion=((public_row or {}).get("payload") or {}).get("promotion", {}),
            registry=((public_row or {}).get("payload") or {}).get("registry", {}),
        ) if public_row else None
        private_fingerprint = self._fingerprint_value((private_row or {}).get("payload"))
        public_fingerprint = self._fingerprint_value((public_row or {}).get("payload"))
        if resolved_scope == "public" and private_fingerprint and public_fingerprint and private_fingerprint != public_fingerprint:
            return {
                "winner": "public",
                "reason": "public_memory_stronger_than_private_on_fingerprint_conflict",
                "private_trust_score": private_quality.get("trust_score") if private_quality else None,
                "public_trust_score": public_quality.get("trust_score") if public_quality else None,
            }
        return {
            "winner": resolved_scope,
            "reason": "private_preferred_in_auto_scope" if resolved_scope == "private" else "public_selected",
            "private_trust_score": private_quality.get("trust_score") if private_quality else None,
            "public_trust_score": public_quality.get("trust_score") if public_quality else None,
        }

    @staticmethod
    def _normalize_selector_record(info: dict) -> dict | None:
        selector_type = (info.get("type") or "").strip()
        selector = (info.get("selector") or "").strip()
        confidence = info.get("confidence", 0.0)
        if not selector_type or not selector:
            return None

        if selector_type == "role":
            parsed = AtlasQualityMixin._parse_role_selector(selector)
            if parsed:
                role, name = parsed
                if role == "inputtime":
                    return {"type": "css", "selector": "input[type=time]", "confidence": confidence}
                if role == "text":
                    return {"type": "text", "selector": name, "confidence": confidence}
                if role in VALID_ROLE_NAMES:
                    return {"type": "role", "selector": f"{role}+{name}", "confidence": confidence}
                return None

        if selector_type == "text":
            text_value = AtlasQualityMixin._parse_text_selector(selector)
            if text_value:
                return {"type": "text", "selector": text_value, "confidence": confidence}

        if selector_type in {"css", "aria_label", "data_testid"}:
            return {"type": selector_type, "selector": selector, "confidence": confidence}

        if selector.lower().startswith("text["):
            text_value = AtlasQualityMixin._parse_text_selector(selector)
            if text_value:
                return {"type": "text", "selector": text_value, "confidence": confidence}

        parsed = AtlasQualityMixin._parse_role_selector(selector)
        if parsed:
            role, name = parsed
            if role == "inputtime":
                return {"type": "css", "selector": "input[type=time]", "confidence": confidence}
            if role == "text":
                return {"type": "text", "selector": name, "confidence": confidence}
            if role in VALID_ROLE_NAMES:
                return {"type": "role", "selector": f"{role}+{name}", "confidence": confidence}

        return None

    @staticmethod
    def _parse_role_selector(selector: str) -> tuple[str, str] | None:
        raw = (selector or "").strip()
        if not raw:
            return None
        if raw.startswith("role="):
            raw = raw[len("role="):].strip()
        if "+" in raw:
            role, name = raw.split("+", 1)
            role = role.strip().lower()
            name = name.strip().strip("'\"")
            return (role, name) if role and name else None
        bracket_match = re.match(r"^\s*([A-Za-z_][\w-]*)\s*\[\s*name\s*=\s*['\"](.+?)['\"]\s*\]\s*$", raw)
        if bracket_match:
            role = bracket_match.group(1).strip().lower()
            name = bracket_match.group(2).strip()
            return (role, name) if role and name else None
        return None

    @staticmethod
    def _parse_text_selector(selector: str) -> str | None:
        raw = (selector or "").strip()
        if not raw:
            return None
        if raw.startswith("text["):
            match = re.match(r"^text\s*\[\s*name\s*=\s*['\"](.+?)['\"]\s*\]\s*$", raw, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return raw.strip("'\"")
