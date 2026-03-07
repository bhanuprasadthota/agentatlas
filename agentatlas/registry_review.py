from agentatlas.registry_common import DEFAULT_TASK_KEY, DEFAULT_VARIANT_KEY


class AtlasReviewMixin:
    def list_review_queue(
        self,
        tenant_id: str | None = None,
        registry_scope: str = "public",
        limit: int = 50,
    ) -> list[dict]:
        try:
            rows = (
                self.sb.table("playbooks")
                .select("id, payload, confidence, variant_key, version, site_id, route_id, task_id")
                .eq("status", "active")
                .order("confidence", desc=False)
                .limit(limit * 3)
                .execute()
                .data
            )
        except Exception:
            return []
        scoped_rows = self._filter_playbooks_by_scope(rows or [], tenant_id=tenant_id, registry_scope=registry_scope)
        queue = []
        for row in scoped_rows:
            payload = row.get("payload") or {}
            promotion = payload.get("promotion", {})
            if promotion.get("review_status") != "review_required":
                continue
            context = self.get_playbook_context(row["id"])
            queue.append({
                "playbook_id": row["id"],
                "site": context.get("site") if context else None,
                "url": context.get("url") if context else None,
                "route_key": context.get("route_key") if context else None,
                "variant_key": row.get("variant_key"),
                "confidence": row.get("confidence"),
                "review_status": promotion.get("review_status"),
                "review_reason": promotion.get("review_reason"),
                "registry_scope": (payload.get("registry") or {}).get("scope", "public"),
                "tenant_id": (payload.get("registry") or {}).get("tenant_id"),
            })
            if len(queue) >= limit:
                break
        return queue

    def list_review_audit(
        self,
        tenant_id: str | None = None,
        registry_scope: str = "auto",
        playbook_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        try:
            query = (
                self.sb.table("playbooks")
                .select("id, payload, confidence, variant_key, version, site_id, route_id, task_id")
                .eq("status", "active")
                .order("id", desc=True)
            )
            if playbook_id is not None:
                query = query.eq("id", playbook_id)
            rows = query.limit(limit * 3).execute().data
        except Exception:
            return []
        scoped_rows = self._filter_playbooks_by_scope(rows or [], tenant_id=tenant_id, registry_scope=registry_scope)
        audit_events = []
        for row in scoped_rows:
            payload = row.get("payload") or {}
            promotion = payload.get("promotion") or {}
            context = self.get_playbook_context(row["id"])
            for event in reversed(promotion.get("audit_trail", [])[-50:]):
                audit_events.append({
                    "playbook_id": row["id"],
                    "site": context.get("site") if context else None,
                    "url": context.get("url") if context else None,
                    "route_key": context.get("route_key") if context else None,
                    "variant_key": row.get("variant_key"),
                    "tenant_id": (payload.get("registry") or {}).get("tenant_id"),
                    "registry_scope": (payload.get("registry") or {}).get("scope", "public"),
                    **event,
                })
        audit_events.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return audit_events[:limit]

    def promote_playbook(
        self,
        playbook_id,
        reviewer: str,
        approved: bool = True,
        notes: str = "",
    ) -> bool:
        context = self.get_playbook_context(playbook_id)
        if not context:
            return False
        reviewer_role = self._get_reviewer_role(reviewer)
        if reviewer_role not in {"admin", "reviewer"}:
            return False
        payload = context["payload"] or {}
        promotion = payload.get("promotion") or {}
        previous_status = promotion.get("review_status", "review_required")
        audit_trail = promotion.get("audit_trail", [])
        promotion.update(
            {
                "review_status": "approved" if approved else "rejected",
                "reviewed_by": reviewer,
                "reviewer_role": reviewer_role,
                "reviewed_at": self._now_iso(),
                "review_notes": notes,
            }
        )
        audit_trail.append(
            {
                "timestamp": self._now_iso(),
                "reviewer": reviewer,
                "reviewer_role": reviewer_role,
                "action": "approved" if approved else "rejected",
                "previous_status": previous_status,
                "new_status": promotion["review_status"],
                "notes": notes,
            }
        )
        promotion["audit_trail"] = audit_trail[-50:]
        payload["promotion"] = promotion
        payload["quality"] = self._compute_quality_summary(
            confidence=payload.get("quality", {}).get("trust_score", 0.6),
            validation=payload.get("validation", {}),
            telemetry=payload.get("telemetry", {}),
            promotion=promotion,
            registry=payload.get("registry", {}),
        )
        self.sb.table("playbooks").update({
            "payload": payload,
            "confidence": payload["quality"]["trust_score"],
        }).eq("id", playbook_id).execute()
        return True

    def get_route_scope_diff(
        self,
        site: str,
        url: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
    ) -> dict | None:
        route = self._find_route(site, url)
        if not route:
            return None
        task_rows = (
            self.sb.table("tasks")
            .select("id")
            .eq("task_key", task_key)
            .limit(1)
            .execute()
            .data
        )
        if not task_rows:
            return None
        playbooks = (
            self.sb.table("playbooks")
            .select("id, payload, confidence, variant_key, version")
            .eq("site_id", route["site_id"])
            .eq("route_id", route["id"])
            .eq("task_id", task_rows[0]["id"])
            .eq("status", "active")
            .limit(50)
            .execute()
            .data
        )
        private_rows = self._rank_playbooks_for_variant(
            self._filter_playbooks_by_scope(playbooks, tenant_id=tenant_id, registry_scope="private"),
            variant_key=variant_key,
        )
        public_rows = self._rank_playbooks_for_variant(
            self._filter_playbooks_by_scope(playbooks, tenant_id=tenant_id, registry_scope="public"),
            variant_key=variant_key,
        )
        private_best = private_rows[0] if private_rows else None
        public_best = public_rows[0] if public_rows else None
        resolved_rows = self._resolve_scope_conflicts(
            playbooks=playbooks,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope="auto",
        )
        resolved_best = resolved_rows[0] if resolved_rows else None
        if not private_best and not public_best:
            return None
        return {
            "site": site,
            "url": url,
            "task_key": task_key,
            "variant_key": variant_key,
            "tenant_id": tenant_id,
            "decision": self._explain_scope_conflict(
                private_row=private_best,
                public_row=public_best,
                resolved_row=resolved_best,
            ),
            "private": self._playbook_diff_summary(private_best),
            "public": self._playbook_diff_summary(public_best),
            "route_differences": self._diff_locator_sets(private_best, public_best),
        }
