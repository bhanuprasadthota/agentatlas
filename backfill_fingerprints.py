import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from agentatlas import Atlas


load_dotenv(Path(__file__).with_name(".env"))


async def backfill(limit: int = 100) -> list[dict]:
    atlas = Atlas()
    candidates = atlas.registry.list_active_playbooks_missing_fingerprint(limit=limit)
    results = []
    for candidate in candidates:
        context = atlas.registry.get_playbook_context(candidate["id"])
        if not context:
            results.append({
                "playbook_id": candidate["id"],
                "status": "failed",
                "message": "Missing site/route context.",
            })
            continue
        try:
            report = await atlas.validate(
                site=context["site"],
                url=context["url"],
                variant_key=context["variant_key"],
                persist=True,
                relearn_on_degraded=False,
                headless=True,
            )
        except Exception as exc:
            results.append({
                "playbook_id": context["playbook_id"],
                "site": context["site"],
                "url": context["url"],
                "status": "failed",
                "message": str(exc),
            })
            continue
        fingerprint_value = report.current_fingerprint
        if fingerprint_value:
            updated = atlas.registry.backfill_playbook_fingerprint(
                playbook_id=context["playbook_id"],
                fingerprint={
                    "algorithm": "acc_tree_v1",
                    "value": fingerprint_value,
                    "path_signature": "",
                    "node_count": 0,
                },
                source="fingerprint_backfill",
            )
            results.append({
                "playbook_id": context["playbook_id"],
                "site": context["site"],
                "url": context["url"],
                "status": "backfilled" if updated else "failed",
                "validation_status": report.status,
                "fingerprint": fingerprint_value,
            })
        else:
            results.append({
                "playbook_id": context["playbook_id"],
                "site": context["site"],
                "url": context["url"],
                "status": "failed",
                "validation_status": report.status,
                "message": "Validation did not produce a fingerprint.",
            })
    return results


def main() -> int:
    limit = int(os.getenv("AGENTATLAS_BACKFILL_LIMIT", "100"))
    results = asyncio.run(backfill(limit=limit))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
