import json
import os
from pathlib import Path

from dotenv import load_dotenv

from agentatlas import Atlas


load_dotenv(Path(__file__).with_name(".env"))


def compare_runs(suite_name: str = "warm_start_reliability") -> dict:
    atlas = Atlas()
    runs = atlas.registry.list_benchmark_runs(
        suite_name=suite_name,
        limit=2,
        tenant_id=atlas.tenant_id,
    )
    if len(runs) < 2:
        return {
            "suite_name": suite_name,
            "status": "insufficient_data",
            "message": "Need at least two benchmark runs to compare.",
        }

    latest, previous = runs[0], runs[1]
    previous_by_name = {item["name"]: item for item in previous.get("payload", [])}
    regressions = []

    for item in latest.get("payload", []):
        prior = previous_by_name.get(item["name"])
        if not prior:
            continue

        if item.get("validation_status") != "healthy" and prior.get("validation_status") == "healthy":
            regressions.append({
                "workflow": item["name"],
                "kind": "validation_status",
                "previous": prior.get("validation_status"),
                "current": item.get("validation_status"),
            })

        if bool(item.get("warm_registry_hit")) is False and bool(prior.get("warm_registry_hit")) is True:
            regressions.append({
                "workflow": item["name"],
                "kind": "warm_registry_hit",
                "previous": prior.get("warm_registry_hit"),
                "current": item.get("warm_registry_hit"),
            })

        prior_failed = len(prior.get("failed_locators") or [])
        current_failed = len(item.get("failed_locators") or [])
        if current_failed > prior_failed:
            regressions.append({
                "workflow": item["name"],
                "kind": "failed_locator_count",
                "previous": prior_failed,
                "current": current_failed,
            })

    latest_warm = latest.get("warm_hit_rate")
    previous_warm = previous.get("warm_hit_rate")
    summary = {
        "suite_name": suite_name,
        "tenant_id": atlas.tenant_id,
        "latest_run_at": latest.get("run_at"),
        "previous_run_at": previous.get("run_at"),
        "latest_warm_hit_rate": latest_warm,
        "previous_warm_hit_rate": previous_warm,
        "latest_healthy_count": latest.get("healthy_count"),
        "previous_healthy_count": previous.get("healthy_count"),
        "regressions": regressions,
        "status": "regression" if regressions else "stable",
    }
    return summary


def main() -> int:
    suite_name = os.getenv("AGENTATLAS_BENCHMARK_SUITE", "warm_start_reliability")
    summary = compare_runs(suite_name=suite_name)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("status") != "regression" else 2


if __name__ == "__main__":
    raise SystemExit(main())
