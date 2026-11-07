#!/usr/bin/env python3
"""Queue a surveillance JSON update without rebuilding the static site."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.data_release_service import data_release_service


async def main(job_id):
    result = await data_release_service.trigger_job(job_id, manual=True,
        trigger="surveillance-json-cli", data_only=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", default="site-release")
    asyncio.run(main(parser.parse_args().job_id))
