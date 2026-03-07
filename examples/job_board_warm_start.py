import asyncio
import json
import os
from dataclasses import asdict, is_dataclass

from agentatlas import Atlas

JOB_BOARD_URLS = {
    "greenhouse": "https://boards.greenhouse.io/anthropic",
    "lever": "https://jobs.lever.co/scaleai",
}


def serialize(value):
    if is_dataclass(value):
        return asdict(value)
    return value


async def main() -> None:
    board = (os.getenv("AGENTATLAS_DEMO_BOARD") or "greenhouse").strip().lower()
    url = os.getenv("AGENTATLAS_DEMO_URL") or JOB_BOARD_URLS.get(board)
    if not url:
        raise SystemExit("Set AGENTATLAS_DEMO_URL or use AGENTATLAS_DEMO_BOARD=greenhouse|lever.")

    site = (os.getenv("AGENTATLAS_DEMO_SITE") or url.split("/")[2]).replace("www.", "")
    registry_scope = (os.getenv("AGENTATLAS_DEMO_REGISTRY_SCOPE") or "private").strip().lower()
    tenant_id = os.getenv("AGENTATLAS_DEMO_TENANT_ID") or "demo-local"
    use_api = bool(os.getenv("AGENTATLAS_API_URL"))
    atlas = Atlas(use_api=use_api, tenant_id=tenant_id, registry_scope=registry_scope)

    first = await atlas.get_schema(site=site, url=url, tenant_id=tenant_id, registry_scope=registry_scope)
    second = await atlas.get_schema(site=site, url=url, tenant_id=tenant_id, registry_scope=registry_scope)
    playbook = await atlas.get_playbook(site=site, url=url, tenant_id=tenant_id, registry_scope=registry_scope)

    result = {
        "site": site,
        "url": url,
        "registry_scope": registry_scope,
        "tenant_id": tenant_id,
        "first_lookup": serialize(first),
        "second_lookup": serialize(second),
        "playbook": serialize(playbook),
        "warm_hit": second.source == "registry" and second.tokens_used == 0,
        "element_count": len(second.elements or {}),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
