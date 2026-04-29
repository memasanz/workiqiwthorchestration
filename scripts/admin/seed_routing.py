"""Seed the Cosmos `routing` container with default tax + legal queues.

Reads cosmos endpoint + DB from azd env values; uses DefaultAzureCredential.
Idempotent — re-run safely after changing user lists.

Usage:
    python scripts/admin/seed_routing.py
or:
    pwsh ./scripts/admin/seed_routing.ps1
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from azure.cosmos.aio import CosmosClient
from azure.cosmos import exceptions as cex
from azure.identity.aio import DefaultAzureCredential


ROUTING_DOCS = [
    {
        "id": "tax",
        "category": "tax",
        "userIds": ["maya@contoso.com", "alex@contoso.com"],
        "roundRobinIndex": 0,
    },
    {
        "id": "legal",
        "category": "legal",
        "userIds": ["devon@contoso.com", "rae@contoso.com"],
        "roundRobinIndex": 0,
    },
]


def _azd_env() -> dict[str, str]:
    out = subprocess.check_output(["azd", "env", "get-values"], text=True, shell=True)
    env: dict[str, str] = {}
    for line in out.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"')
    return env


async def main() -> int:
    env = _azd_env()
    endpoint = env.get("cosmosEndpoint") or env.get("COSMOS_ENDPOINT")
    database = env.get("cosmosDatabase") or env.get("COSMOS_DATABASE", "workflow")
    if not endpoint:
        print("ERROR: cosmosEndpoint not in azd env. Run `azd env refresh`.", file=sys.stderr)
        return 1

    print(f"Cosmos endpoint: {endpoint}")
    print(f"Database:        {database}")

    cred = DefaultAzureCredential()
    client = CosmosClient(endpoint, credential=cred)
    try:
        db = client.get_database_client(database)
        routing = db.get_container_client("routing")
        for doc in ROUTING_DOCS:
            try:
                await routing.upsert_item(doc)
                print(f"  ✓ seeded routing/{doc['id']}: {doc['userIds']}")
            except cex.CosmosHttpResponseError as e:
                print(f"  ✗ ERROR seeding {doc['id']}: {e.message}", file=sys.stderr)
                return 2
    finally:
        await client.close()
        await cred.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
