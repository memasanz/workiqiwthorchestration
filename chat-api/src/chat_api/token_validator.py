"""Validate Entra-issued bearer tokens (v2.0) presented by the SPA.

Validates against the published JWKS for the tenant, checks issuer,
audience (accepts both raw guid and ``api://<guid>`` form) and that the
caller has consented to the required scope (``Chat.ReadWrite``).
"""
from __future__ import annotations

import logging
from typing import Any

import jwt
from jwt import PyJWKClient

log = logging.getLogger(__name__)


class TokenValidator:
    def __init__(self, tenant_id: str, backend_client_id: str, required_scope: str) -> None:
        self.tenant_id = tenant_id
        self.backend_client_id = backend_client_id
        self.required_scope = required_scope
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.audiences = [backend_client_id, f"api://{backend_client_id}"]
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        # PyJWKClient handles its own caching of the JWKS document.
        self._jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)

    def validate(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token).key
        except Exception as e:  # noqa: BLE001
            raise PermissionError(f"Failed to resolve signing key: {e}") from e

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self.audiences,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.InvalidTokenError as e:
            raise PermissionError(f"Invalid bearer token: {e}") from e

        scp_raw = claims.get("scp") or claims.get("scope") or ""
        scopes = scp_raw.split() if isinstance(scp_raw, str) else list(scp_raw)
        if self.required_scope not in scopes:
            raise PermissionError(
                f"Token missing required scope '{self.required_scope}' (have: {scopes})"
            )
        if not claims.get("oid"):
            raise ValueError("Token has no 'oid' claim")
        return claims
