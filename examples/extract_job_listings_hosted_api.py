"""Hosted API wrapper for the job listing extraction demo.

Usage:
    export AGENTATLAS_API_URL=https://your-api.fly.dev
    export AGENTATLAS_API_KEY=your-key
    export AGENTATLAS_TENANT_ID=your-tenant
    python3 examples/extract_job_listings_hosted_api.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.extract_job_listings import main as extract_main


async def main() -> None:
    api_url = os.getenv("AGENTATLAS_API_URL", "").strip()
    if not api_url:
        raise SystemExit(
            "Missing required environment variable: AGENTATLAS_API_URL\n"
            "Set it and retry:\n"
            "    export AGENTATLAS_API_URL=..."
        )
    try:
        with urlopen(f"{api_url.rstrip('/')}/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        print(f"Hosted API health: {health}")
    except Exception as exc:
        raise SystemExit(f"Hosted API unreachable at {api_url}: {exc}")
    await extract_main()


if __name__ == "__main__":
    asyncio.run(main())
