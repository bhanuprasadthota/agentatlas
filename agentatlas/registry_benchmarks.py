from datetime import datetime, timedelta, timezone


class AtlasBenchmarkMixin:
    def persist_benchmark_run(
        self,
        suite_name: str,
        results: list[dict],
        metadata: dict | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        workflow_count = len(results)
        healthy_count = sum(1 for item in results if item.get("validation_status") == "healthy")
        degraded_count = sum(1 for item in results if item.get("validation_status") == "degraded")
        failed_count = sum(1 for item in results if item.get("validation_status") == "failed")
        warm_hits = sum(1 for item in results if item.get("warm_registry_hit"))
        warm_hit_rate = (warm_hits / workflow_count) if workflow_count else None
        merged_metadata = dict(metadata or {})
        if tenant_id:
            merged_metadata["tenant_id"] = tenant_id
        row = {
            "suite_name": suite_name,
            "workflow_count": workflow_count,
            "healthy_count": healthy_count,
            "degraded_count": degraded_count,
            "failed_count": failed_count,
            "warm_hit_rate": warm_hit_rate,
            "payload": results,
            "metadata": merged_metadata,
        }
        try:
            self.sb.table("benchmark_runs").insert(row).execute()
            return True
        except Exception:
            return False

    def list_benchmark_runs(self, suite_name: str, limit: int = 2, tenant_id: str | None = None) -> list[dict]:
        try:
            rows = (
                self.sb.table("benchmark_runs")
                .select("id, suite_name, run_at, workflow_count, healthy_count, degraded_count, failed_count, warm_hit_rate, payload, metadata")
                .eq("suite_name", suite_name)
                .order("run_at", desc=True)
                .limit(limit * 5 if tenant_id else limit)
                .execute()
                .data
            )
            if not tenant_id:
                return rows
            filtered = [row for row in rows if (row.get("metadata") or {}).get("tenant_id") == tenant_id]
            return filtered[:limit]
        except Exception:
            return []

    def get_benchmark_dashboard(
        self,
        suite_name: str,
        tenant_id: str | None = None,
        limit: int = 10,
    ) -> dict:
        runs = self.list_benchmark_runs(suite_name=suite_name, limit=limit, tenant_id=tenant_id)
        if not runs:
            return {
                "suite_name": suite_name,
                "tenant_id": tenant_id,
                "run_count": 0,
                "latest_run_at": None,
                "latest_status": "no_data",
                "warm_hit_rate_trend": [],
                "healthy_count_trend": [],
                "categories": {},
            }

        latest = runs[0]
        categories: dict[str, dict] = {}
        for item in latest.get("payload", []):
            category = item.get("category", "general")
            bucket = categories.setdefault(
                category,
                {
                    "workflow_count": 0,
                    "healthy_count": 0,
                    "degraded_count": 0,
                    "failed_count": 0,
                    "warm_hits": 0,
                },
            )
            bucket["workflow_count"] += 1
            if item.get("warm_registry_hit"):
                bucket["warm_hits"] += 1
            status = item.get("validation_status")
            if status == "healthy":
                bucket["healthy_count"] += 1
            elif status == "degraded":
                bucket["degraded_count"] += 1
            else:
                bucket["failed_count"] += 1

        for bucket in categories.values():
            workflow_count = bucket["workflow_count"] or 1
            bucket["warm_hit_rate"] = bucket["warm_hits"] / workflow_count

        return {
            "suite_name": suite_name,
            "tenant_id": tenant_id,
            "run_count": len(runs),
            "latest_run_at": latest.get("run_at"),
            "latest_status": "healthy" if latest.get("failed_count", 0) == 0 and latest.get("degraded_count", 0) == 0 else "attention",
            "warm_hit_rate_trend": [run.get("warm_hit_rate") for run in reversed(runs)],
            "healthy_count_trend": [run.get("healthy_count") for run in reversed(runs)],
            "categories": categories,
        }

    def list_revalidation_candidates(
        self,
        max_age_hours: int = 24,
        limit: int = 25,
        tenant_id: str | None = None,
        registry_scope: str = "auto",
    ) -> list[dict]:
        try:
            rows = (
                self.sb.table("playbooks")
                .select("id, payload, confidence, variant_key, version, site_id, route_id, task_id")
                .eq("status", "active")
                .order("confidence", desc=False)
                .limit(limit * 6)
                .execute()
                .data
            )
        except Exception:
            return []
        scoped_rows = self._filter_playbooks_by_scope(rows or [], tenant_id=tenant_id, registry_scope=registry_scope)
        candidates = []
        for row in scoped_rows:
            context = self.get_playbook_context(row["id"])
            if not context:
                continue
            payload = row.get("payload") or {}
            validation = self._get_latest_validation_summary(
                playbook_payload=payload,
                site_id=row["site_id"],
                route_id=row["route_id"],
                task_id=row["task_id"],
                variant_key=row.get("variant_key"),
            )
            due_reason = self._revalidation_due_reason(validation, max_age_hours=max_age_hours)
            if not due_reason:
                continue
            candidates.append(
                {
                    "playbook_id": row["id"],
                    "site": context.get("site"),
                    "url": context.get("url"),
                    "route_key": context.get("route_key"),
                    "variant_key": row.get("variant_key"),
                    "tenant_id": (payload.get("registry") or {}).get("tenant_id"),
                    "registry_scope": (payload.get("registry") or {}).get("scope", "public"),
                    "validation_status": validation.get("status"),
                    "last_validated_at": validation.get("last_validated_at"),
                    "revalidation_reason": due_reason,
                    "trust_score": (payload.get("quality") or {}).get("trust_score", row.get("confidence", 0.0)),
                }
            )
            if len(candidates) >= limit:
                break
        return candidates

    @staticmethod
    def _revalidation_due_reason(validation: dict | None, max_age_hours: int) -> str | None:
        validation = validation or {}
        status = (validation.get("status") or "").strip().lower()
        if status in {"stale", "failed", "degraded"}:
            return f"status:{status}"
        last_validated_at = validation.get("last_validated_at")
        if not last_validated_at:
            return "missing_validation"
        try:
            parsed = datetime.fromisoformat(last_validated_at.replace("Z", "+00:00"))
        except Exception:
            return "invalid_validation_timestamp"
        age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        if age >= timedelta(hours=max_age_hours):
            return f"age:{int(age.total_seconds() // 3600)}h"
        return None
