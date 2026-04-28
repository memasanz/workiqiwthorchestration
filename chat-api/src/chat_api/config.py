"""Centralized environment configuration."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Config:
    foundry_project_endpoint: str
    model_deployment_name: str
    submissions_mcp_url: str
    tax_mcp_url: str
    legal_mcp_url: str
    dev_bypass_auth: bool
    log_level: str
    # Iteration 5 — end-user identity passthrough.
    entra_tenant_id: str = ""
    entra_backend_client_id: str = ""
    entra_required_scope: str = "Chat.ReadWrite"
    managed_identity_client_id: str = ""
    token_validator: Any = field(default=None)
    user_cred_factory: Any = field(default=None)

    @property
    def azure_openai_endpoint(self) -> str:
        # FOUNDRY_PROJECT_ENDPOINT looks like
        # https://<account>.services.ai.azure.com/api/projects/<project>
        # Strip down to the account root if a caller wants to talk to AOAI directly.
        ep = self.foundry_project_endpoint
        if "/api/projects/" in ep:
            return ep.split("/api/projects/")[0]
        return ep.rstrip("/")

    @property
    def mcp_urls(self) -> dict[str, str]:
        return {
            "submissions": self.submissions_mcp_url,
            "tax_sme": self.tax_mcp_url,
            "legal_sme": self.legal_mcp_url,
        }


def load_config() -> Config:
    missing: list[str] = []

    def need(key: str) -> str:
        v = os.environ.get(key, "").strip()
        if not v:
            missing.append(key)
        return v

    cfg = Config(
        foundry_project_endpoint=need("FOUNDRY_PROJECT_ENDPOINT"),
        model_deployment_name=need("MODEL_DEPLOYMENT_NAME"),
        submissions_mcp_url=need("SUBMISSIONS_MCP_URL"),
        tax_mcp_url=need("TAX_MCP_URL"),
        legal_mcp_url=need("LEGAL_MCP_URL"),
        dev_bypass_auth=_bool("DEV_BYPASS_AUTH", False),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        entra_tenant_id=os.environ.get("ENTRA_TENANT_ID", "").strip(),
        entra_backend_client_id=os.environ.get("ENTRA_BACKEND_CLIENT_ID", "").strip(),
        entra_required_scope=os.environ.get("ENTRA_REQUIRED_SCOPE", "Chat.ReadWrite").strip(),
        managed_identity_client_id=os.environ.get("MANAGED_IDENTITY_CLIENT_ID", "").strip(),
    )
    if missing and not _bool("CHAT_API_ALLOW_MISSING_ENV", False):
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    if cfg.entra_tenant_id and cfg.entra_backend_client_id and cfg.managed_identity_client_id:
        # Local imports to avoid pulling jwt/azure-identity at module import time
        # (keeps unit tests light and lets dev bypass mode run without these).
        from .foundry_credential import UserCredentialFactory
        from .token_validator import TokenValidator

        cfg.token_validator = TokenValidator(
            tenant_id=cfg.entra_tenant_id,
            backend_client_id=cfg.entra_backend_client_id,
            required_scope=cfg.entra_required_scope,
        )
        cfg.user_cred_factory = UserCredentialFactory(
            tenant_id=cfg.entra_tenant_id,
            backend_client_id=cfg.entra_backend_client_id,
            uami_client_id=cfg.managed_identity_client_id,
        )
        log.info(
            "JWT validation + OBO+FIC enabled: tenant=%s backendClientId=%s scope=%s uamiClientId=%s",
            cfg.entra_tenant_id, cfg.entra_backend_client_id, cfg.entra_required_scope,
            cfg.managed_identity_client_id,
        )
    elif not cfg.dev_bypass_auth:
        log.warning(
            "No Entra config and DEV_BYPASS_AUTH=false — all requests will be rejected. "
            "Set ENTRA_TENANT_ID, ENTRA_BACKEND_CLIENT_ID, MANAGED_IDENTITY_CLIENT_ID."
        )
    return cfg


# Mapping from a chat-api "agent_id" to the MCP backend profile key.
# As of 0.4.0 this mapping is no longer used to wire MCP tools (those live on
# the registered Foundry agents). It is preserved for handoff routing/labels
# and for the /health endpoint snapshot.
AGENT_TO_MCP_PROFILE = {
    "submissions": "submissions",
    "tax": "tax_sme",
    "legal": "legal_sme",
}

# Mapping from a chat-api "agent_id" to the registered Foundry agent name
# (created server-side with MCPTool(require_approval="never") attached).
AGENT_TO_FOUNDRY_NAME = {
    "submissions": "submissions-agent",
    "tax": "tax-sme-agent",
    "legal": "legal-sme-agent",
}

# Tools that require human approval before execution (per brief).
DESTRUCTIVE_TOOLS = {
    "create_project",
    "submit_questions",
    "update_project_status",
    "submit_answer",
}

# Tools that should auto-execute server-side (per brief).
AUTO_TOOLS = {
    "get_routing",
    "get_my_assignments",
    "get_question",
    "get_project",
    "save_draft",
    "update_question_status",
    "assign_question",
    "set_question_classification",
}

