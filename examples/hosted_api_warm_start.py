"""
examples/hosted_api_warm_start.py

Demonstrates AgentAtlas warm-start behavior using the hosted API.
Requires a running AgentAtlas API server.

Quick start:
    export AGENTATLAS_API_URL=https://your-api.fly.dev
    export AGENTATLAS_API_KEY=your-key
    export AGENTATLAS_TENANT_ID=your-tenant      # optional
    python3 examples/hosted_api_warm_start.py

Self-host locally:
    uvicorn agentatlas.api:app --port 8000
    export AGENTATLAS_API_URL=http://localhost:8000
    python3 examples/hosted_api_warm_start.py

What this shows:
    Run 1 - cold start: API learns the page, pays tokens once, saves to registry
    Run 2 - warm start: API serves from registry, 0 tokens, instant

The warm-hit is shared across all developers hitting the same API.
"""

import asyncio
import json
import os
import urllib.request

from agentatlas import Atlas

JOB_BOARD_URLS = {
    "greenhouse": "https://boards.greenhouse.io/anthropic",
    "lever": "https://jobs.lever.co/scaleai",
}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(
            f"Missing required environment variable: {name}\n"
            f"Set it and retry:\n"
            f"    export {name}=..."
        )
    return value


async def main() -> None:
    api_url = require_env("AGENTATLAS_API_URL")
    api_key = os.getenv("AGENTATLAS_API_KEY", "").strip() or None
    tenant_id = os.getenv("AGENTATLAS_TENANT_ID", "").strip() or None

    board = (os.getenv("AGENTATLAS_DEMO_BOARD") or "greenhouse").strip().lower()
    url = os.getenv("AGENTATLAS_DEMO_URL") or JOB_BOARD_URLS.get(board)
    if not url:
        raise SystemExit("Set AGENTATLAS_DEMO_URL or AGENTATLAS_DEMO_BOARD=greenhouse|lever.")

    site = (os.getenv("AGENTATLAS_DEMO_SITE") or url.split("/")[2]).replace("www.", "")

    atlas = Atlas(
        api_url=api_url,
        api_key=api_key,
        tenant_id=tenant_id,
        use_api=True,
        registry_scope="auto",
    )

    print("AgentAtlas hosted API demo")
    print(f"  API:    {api_url}")
    print(f"  Site:   {site}")
    print(f"  URL:    {url}")
    print()

    try:
        with urllib.request.urlopen(f"{api_url}/health", timeout=5) as resp:
            health = json.loads(resp.read())
        print(f"Health: {health}")
    except Exception as exc:
        raise SystemExit(f"API unreachable at {api_url}: {exc}")
    print()

    print("Run 1 - cold start (may take 10-20s on unknown pages)...")
    first = await atlas.get_schema(site=site, url=url)
    print(f"  source      : {first.source}")
    print(f"  tokens_used : {first.tokens_used}")
    print(f"  elements    : {len(first.elements or {})}")
    print(f"  status      : {first.status}")
    print()

    print("Run 2 - warm start (should be instant, 0 tokens)...")
    second = await atlas.get_schema(site=site, url=url)
    print(f"  source      : {second.source}")
    print(f"  tokens_used : {second.tokens_used}")
    print(f"  elements    : {len(second.elements or {})}")
    print(f"  status      : {second.status}")
    print()

    warm_hit = second.source == "registry" and second.tokens_used == 0
    print(f"Warm hit: {'yes' if warm_hit else 'no - check registry scope and review policy'}")
    print()

    if warm_hit:
        print("Element names available for automation:")
        for name in list((second.elements or {}).keys())[:10]:
            print(f"  {name}")
    else:
        print("Tip: public job-board learns are review-gated.")
        print("     Set AGENTATLAS_TENANT_ID and the API will use private scope for your tenant.")

    output = {
        "site": site,
        "url": url,
        "api_url": api_url,
        "tenant_id": tenant_id,
        "first_lookup": {"source": first.source, "tokens_used": first.tokens_used, "element_count": len(first.elements or {})},
        "second_lookup": {"source": second.source, "tokens_used": second.tokens_used, "element_count": len(second.elements or {})},
        "warm_hit": warm_hit,
    }
    print()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
