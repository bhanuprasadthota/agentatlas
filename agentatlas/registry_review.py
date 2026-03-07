from datetime import datetime, timezone

from agentatlas.registry_common import DEFAULT_TASK_KEY, DEFAULT_VARIANT_KEY


class AtlasReviewMixin:
    def _review_sla_hours(self) -> int:
        import os

        return int(os.getenv("AGENTATLAS_REVIEW_SLA_HOURS", "24"))

    def list_review_queue(
        self,
        tenant_id: str | None = None,
        registry_scope: str = "public",
        limit: int = 50,
    ) -> list[dict]:
        try:
            rows = (
                self.sb.table("playbooks")
                .select("id, payload, confidence, variant_key, version, site_id, route_id, task_id, created_at")
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
        sla_hours = self._review_sla_hours()
        for row in scoped_rows:
            payload = row.get("payload") or {}
            promotion = payload.get("promotion", {})
            if promotion.get("review_status") != "review_required":
                continue
            context = self.get_playbook_context(row["id"])
            flag_reports = (promotion.get("flags") or [])[-20:]
            created_at = row.get("created_at") or (context or {}).get("created_at")
            pending_age_hours = None
            overdue = False
            if created_at:
                try:
                    parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    pending_age_hours = round(
                        (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600, 2
                    )
                    overdue = pending_age_hours >= sla_hours
                except Exception:
                    pending_age_hours = None
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
                "pending_age_hours": pending_age_hours,
                "overdue": overdue,
                "flag_count": len(flag_reports),
            })
            if len(queue) >= limit:
                break
        return queue

    def get_review_dashboard(
        self,
        tenant_id: str | None = None,
        registry_scope: str = "public",
        limit: int = 100,
    ) -> dict:
        queue = self.list_review_queue(
            tenant_id=tenant_id,
            registry_scope=registry_scope,
            limit=limit,
        )
        overdue_count = sum(1 for item in queue if item.get("overdue"))
        oldest = max((item.get("pending_age_hours") or 0.0) for item in queue) if queue else 0.0
        flagged_count = sum(1 for item in queue if (item.get("flag_count") or 0) > 0)
        by_reason = {}
        for item in queue:
            reason = item.get("review_reason") or "unknown"
            by_reason[reason] = by_reason.get(reason, 0) + 1
        return {
            "queue_size": len(queue),
            "overdue_count": overdue_count,
            "oldest_pending_hours": round(oldest, 2),
            "sla_hours": self._review_sla_hours(),
            "flagged_count": flagged_count,
            "reasons": by_reason,
        }

    def list_review_audit(
        self,
        tenant_id: str | None = None,
        registry_scope: str = "auto",
        playbook_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        try:
            query = (
                self.sb.table("review_events")
                .select(
                    "playbook_id, occurred_at, site, url, route_key, variant_key, "
                    "tenant_id, registry_scope, reviewer, reviewer_role, action, notes, metadata"
                )
                .order("occurred_at", desc=True)
            )
            if playbook_id is not None:
                query = query.eq("playbook_id", playbook_id)
            rows = query.limit(limit * 3).execute().data
            filtered = []
            for row in rows or []:
                if registry_scope != "auto" and row.get("registry_scope", "public") != registry_scope:
                    continue
                if tenant_id is not None and row.get("tenant_id") != tenant_id:
                    continue
                filtered.append({
                    "playbook_id": row.get("playbook_id"),
                    "site": row.get("site"),
                    "url": row.get("url"),
                    "route_key": row.get("route_key"),
                    "variant_key": row.get("variant_key"),
                    "tenant_id": row.get("tenant_id"),
                    "registry_scope": row.get("registry_scope", "public"),
                    "timestamp": row.get("occurred_at"),
                    "reviewer": row.get("reviewer"),
                    "reviewer_role": row.get("reviewer_role"),
                    "action": row.get("action"),
                    "notes": row.get("notes", ""),
                    "source": "review_events",
                    "metadata": row.get("metadata") or {},
                })
            if filtered:
                return filtered[:limit]
        except Exception:
            pass
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
                    "source": "payload",
                    "metadata": {},
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
        self._insert_review_audit_event(
            playbook_id=playbook_id,
            site=context.get("site"),
            url=context.get("url"),
            route_key=context.get("route_key"),
            variant_key=context.get("variant_key"),
            tenant_id=(payload.get("registry") or {}).get("tenant_id"),
            registry_scope=(payload.get("registry") or {}).get("scope", "public"),
            reviewer=reviewer,
            reviewer_role=reviewer_role,
            action="approved" if approved else "rejected",
            notes=notes,
            metadata={"previous_status": previous_status, "new_status": promotion["review_status"]},
        )
        return True

    def flag_schema(
        self,
        site: str,
        url: str,
        reporter: str,
        reason: str,
        task_key: str = DEFAULT_TASK_KEY,
        variant_key: str = DEFAULT_VARIANT_KEY,
        tenant_id: str | None = None,
        registry_scope: str = "auto",
        notes: str = "",
        metadata: dict | None = None,
    ) -> bool:
        playbook = self.get_playbook(
            site=site,
            url=url,
            task_key=task_key,
            variant_key=variant_key,
            tenant_id=tenant_id,
            registry_scope=registry_scope,
        )
        if not playbook:
            return False
        payload = playbook.metadata or {}
        promotion = payload.get("promotion") or {}
        flags = promotion.get("flags", [])
        registry_meta = payload.get("registry") or {}
        flag_event = {
            "timestamp": self._now_iso(),
            "reporter": reporter,
            "reason": reason,
            "notes": notes,
            "metadata": metadata or {},
        }
        flags.append(flag_event)
        promotion["flags"] = flags[-25:]
        if registry_meta.get("scope", "public") == "public":
            promotion["review_status"] = "review_required"
            promotion["review_reason"] = f"flagged:{reason}"
        payload["promotion"] = promotion
        payload["quality"] = self._compute_quality_summary(
            confidence=playbook.confidence,
            validation=payload.get("validation", {}),
            telemetry=payload.get("telemetry", {}),
            promotion=promotion,
            registry=registry_meta,
        )
        route = self._find_route(site, url)
        if not route:
            return False
        task_rows = (
            self.sb.table("tasks")
            .select("id")
            .eq("task_key", task_key)
            .limit(1)
            .execute()
            .data
        )
        if not task_rows:
            return False
        playbook_rows = (
            self.sb.table("playbooks")
            .select("id, payload, confidence, variant_key")
            .eq("site_id", route["site_id"])
            .eq("route_id", route["id"])
            .eq("task_id", task_rows[0]["id"])
            .eq("variant_key", playbook.variant_key)
            .eq("status", "active")
            .order("version", desc=True)
            .limit(25)
            .execute()
            .data
        )
        scoped = self._filter_playbooks_by_scope(playbook_rows, tenant_id=tenant_id, registry_scope=registry_scope)
        if not scoped:
            return False
        self.sb.table("playbooks").update({
            "payload": payload,
            "confidence": payload["quality"]["trust_score"],
        }).eq("id", scoped[0]["id"]).execute()
        self._insert_review_audit_event(
            playbook_id=scoped[0]["id"],
            site=site,
            url=url,
            route_key=playbook.route_key,
            variant_key=playbook.variant_key,
            tenant_id=registry_meta.get("tenant_id"),
            registry_scope=registry_meta.get("scope", "public"),
            reviewer=reporter,
            reviewer_role="reporter",
            action="flagged",
            notes=notes or reason,
            metadata={"reason": reason, **(metadata or {})},
        )
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
