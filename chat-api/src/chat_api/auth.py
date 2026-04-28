"""Caller identity resolution.

Two modes are supported:

* **Production / OBO mode** — the SPA presents an Entra-issued bearer
  token in ``Authorization: Bearer <jwt>``. We validate it against the
  tenant JWKS and extract caller claims. The raw JWT is preserved on the
  ``CallerIdentity`` so downstream code can build an OBO credential.
* **Dev bypass mode** — set ``DEV_BYPASS_AUTH=true`` and pass
  ``?as_user=<email>``. No real auth, useful for local smoke testing.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import Config


@dataclass(frozen=True)
class CallerIdentity:
    email: str
    name: str
    oid: str
    raw_token: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {"email": self.email, "name": self.name, "oid": self.oid}


def _stable_oid(email: str) -> str:
    h = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
    # Synthesize a GUID-shaped value
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _from_principal(principal: dict) -> CallerIdentity:
    claims = principal.get("claims") or []
    by_typ: dict[str, str] = {c.get("typ", ""): c.get("val", "") for c in claims if c.get("typ")}
    email = (
        by_typ.get("preferred_username")
        or by_typ.get("emails")
        or by_typ.get("email")
        or by_typ.get("upn")
        or principal.get("userDetails")
        or ""
    ).strip()
    name = by_typ.get("name") or principal.get("userDetails") or email
    oid = (
        by_typ.get("http://schemas.microsoft.com/identity/claims/objectidentifier")
        or by_typ.get("oid")
        or principal.get("userId")
        or _stable_oid(email or "anonymous")
    )
    if not email:
        raise HTTPException(status_code=401, detail="No usable email/upn claim in client principal")
    return CallerIdentity(email=email, name=name, oid=oid)


def parse_client_principal_header(b64: str) -> CallerIdentity:
    try:
        decoded = base64.b64decode(b64).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Invalid X-MS-CLIENT-PRINCIPAL header: {e}") from e
    return _from_principal(data)


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def caller_from_bearer(request: Request, validator) -> CallerIdentity:
    """Resolve the caller from a validated Entra bearer token."""
    bearer = _extract_bearer(request)
    if not bearer:
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer header")
    try:
        claims = validator.validate(bearer)
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    email = (claims.get("preferred_username") or claims.get("upn") or "").strip()
    name = claims.get("name") or email
    oid = claims["oid"]
    return CallerIdentity(email=email or oid, name=name, oid=oid, raw_token=bearer)


def caller_from_request(request: Request, cfg: Config) -> CallerIdentity:
    # Back-compat dispatcher used by all routes. Order:
    #   1. Real bearer (when token validator is wired)
    #   2. Easy Auth client-principal header (legacy)
    #   3. Dev bypass via ?as_user=
    if cfg.token_validator is not None:
        return caller_from_bearer(request, cfg.token_validator)
    header = request.headers.get("x-ms-client-principal")
    if header:
        return parse_client_principal_header(header)
    if cfg.dev_bypass_auth:
        as_user = request.query_params.get("as_user")
        if as_user:
            return CallerIdentity(email=as_user, name=as_user, oid=_stable_oid(as_user))
    raise HTTPException(status_code=401, detail="Missing Authorization: Bearer header")


def get_caller(cfg: Config, request: Request) -> CallerIdentity:
    """Public alias for :func:`caller_from_request`."""
    return caller_from_request(request, cfg)

