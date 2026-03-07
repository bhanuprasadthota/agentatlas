"""Hosted API wrapper for the job listing extraction demo.

Usage:
    export AGENTATLAS_API_URL=https://your-api.fly.dev
    export AGENTATLAS_API_KEY=your-key
    export AGENTATLAS_TENANT_ID=your-tenant
    python3 examples/extract_job_listings_hosted_api.py
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.extract_job_listings import main as extract_main


async def main() -> None:
    if not os.getenv("AGENTATLAS_API_URL", "").strip():
        raise SystemExit(
            "Missing required environment variable: AGENTATLAS_API_URL\n"
            "Set it and retry:\n"
            "    export AGENTATLAS_API_URL=..."
        )
    await extract_main()


if __name__ == "__main__":
    asyncio.run(main())
