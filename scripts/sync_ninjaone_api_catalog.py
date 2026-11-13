"""Download NinjaOne's official OpenAPI JSON and build the local API catalogue.

Run from the repository root:
    .\.venv\Scripts\python.exe .\scripts\sync_ninjaone_api_catalog.py
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ninjaone_agent.api_catalog import build_catalog  # noqa: E402

DEFAULT_URL = "https://app.ninjarmm.com/apidocs/NinjaRMM-API-v2.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the local NinjaOne API v2 endpoint/field catalogue.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Official NinjaOne OpenAPI JSON URL.")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "ninjaone_api_catalog.json")
    args = parser.parse_args()

    request = urllib.request.Request(
        args.url,
        headers={"User-Agent": "NinjaOne-Local-AI-Agent API catalogue sync"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            spec = json.loads(response.read().decode("utf-8-sig"))
        catalog = build_catalog(spec)
    except Exception as exc:
        print(f"ERROR: Could not download or parse NinjaOne OpenAPI specification: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Created: {args.output}")
    print(f"API version: {catalog['api_version'] or 'not specified'}")
    print(f"Endpoints indexed: {catalog['endpoint_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
