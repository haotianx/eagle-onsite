"""Print the API's OpenAPI schema as JSON (the web client's types are generated from it)."""

from __future__ import annotations

import json
import sys


def schema() -> dict:
    from fastapi import FastAPI

    from ingest.api.app import api_router

    app = FastAPI(title="ingest")
    app.include_router(api_router())
    return app.openapi()


def main() -> int:
    json.dump(schema(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
