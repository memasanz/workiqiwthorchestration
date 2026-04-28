"""Auth header parsing tests."""
import base64
import json
import os

import pytest
from fastapi import HTTPException

from chat_api.auth import parse_client_principal_header, _stable_oid


def _principal_header(claims: dict[str, str]) -> str:
    payload = {
        "auth_typ": "aad",
        "claims": [{"typ": k, "val": v} for k, v in claims.items()],
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def test_parse_principal_with_preferred_username():
    h = _principal_header({"preferred_username": "alice@contoso.com", "name": "Alice"})
    c = parse_client_principal_header(h)
    assert c.email == "alice@contoso.com"
    assert c.name == "Alice"
    assert c.oid


def test_parse_principal_falls_back_to_upn():
    h = _principal_header({"upn": "bob@contoso.com"})
    c = parse_client_principal_header(h)
    assert c.email == "bob@contoso.com"


def test_parse_principal_invalid_b64_raises():
    with pytest.raises(HTTPException):
        parse_client_principal_header("not-base64!!")


def test_stable_oid_is_deterministic():
    assert _stable_oid("a@b.com") == _stable_oid("a@b.com")
    assert len(_stable_oid("a@b.com").split("-")) == 5
