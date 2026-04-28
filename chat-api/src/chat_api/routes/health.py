"""GET /health — liveness + config snapshot."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()

AGENTS = ["submissions", "tax", "legal"]


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    cfg = request.app.state.cfg
    return {
        "status": "ok",
        "model": cfg.model_deployment_name,
        "modelDeployment": cfg.model_deployment_name,
        "agents": AGENTS,
        "foundryProject": cfg.foundry_project_endpoint,
        "mcpBackends": {
            "submissions": cfg.submissions_mcp_url,
            "tax": cfg.tax_mcp_url,
            "legal": cfg.legal_mcp_url,
        },
        "devBypassAuth": cfg.dev_bypass_auth,
        "jwtAuthEnabled": cfg.token_validator is not None,
        "version": "0.8.4",
    }
