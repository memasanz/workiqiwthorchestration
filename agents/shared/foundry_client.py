"""Tiny factory for the AIProjectClient used by every agent script."""
from __future__ import annotations

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def get_project_client(endpoint: str) -> AIProjectClient:
    """Return an AIProjectClient bound to the given Foundry project endpoint.

    Uses DefaultAzureCredential so it works with `az login` locally and with
    a managed identity in production.
    """
    return AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
