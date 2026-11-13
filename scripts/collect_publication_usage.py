#!/usr/bin/env python3
"""Collect one read-only account usage snapshot; no deployment or mutation."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.data_release.publication_usage import refresh_usage

if __name__ == "__main__":
    print(json.dumps(refresh_usage(), indent=2))
