import asyncio
import json
import os

from agentatlas import Atlas


async def main() -> None:
    atlas = Atlas(use_api=False)
    results = await atlas.run_revalidation_cycle(
        max_age_hours=int(os.getenv("AGENTATLAS_REVALIDATION_MAX_AGE_HOURS", "24")),
        limit=int(os.getenv("AGENTATLAS_REVALIDATION_LIMIT", "25")),
        headless=os.getenv("AGENTATLAS_REVALIDATION_HEADLESS", "1") != "0",
        tenant_id=os.getenv("AGENTATLAS_TENANT_ID"),
        registry_scope=os.getenv("AGENTATLAS_REGISTRY_SCOPE"),
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
