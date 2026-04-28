"""Build per-request OnBehalfOfCredentials backed by a UAMI federated
identity credential (FIC).

Mirrors the C# ``ManagedIdentityClientAssertion`` pattern: a process-wide
``ManagedIdentityCredential`` produces an MI token for the audience
``api://AzureADTokenExchange``; that JWT is presented as the
``client_assertion`` to the OBO flow when exchanging the user's bearer
for a downstream access token.
"""
from __future__ import annotations

import logging
import threading
import time

from azure.identity import ManagedIdentityCredential
from azure.identity.aio import OnBehalfOfCredential

log = logging.getLogger(__name__)

_FIC_AUDIENCE = "api://AzureADTokenExchange/.default"
# Refresh the cached MI assertion ~5 minutes before its real expiry.
_REFRESH_SKEW_SECONDS = 300


class _MiAssertionCache:
    """Cache the FIC assertion (MI-issued JWT) across requests.

    The MI token is cheap-ish but the call is sync I/O — caching avoids
    hitting IMDS on every user request. ``OnBehalfOfCredential`` invokes
    the assertion callback exactly once per token exchange, so this is
    safe.
    """

    def __init__(self, uami_client_id: str) -> None:
        self._cred = ManagedIdentityCredential(client_id=uami_client_id)
        self._lock = threading.Lock()
        self._token: str | None = None
        self._exp: float = 0.0

    def get(self) -> str:
        now = time.time()
        if self._token and now < self._exp - _REFRESH_SKEW_SECONDS:
            return self._token
        with self._lock:
            now = time.time()
            if self._token and now < self._exp - _REFRESH_SKEW_SECONDS:
                return self._token
            tok = self._cred.get_token(_FIC_AUDIENCE)
            self._token = tok.token
            self._exp = float(tok.expires_on)
            return self._token


class UserCredentialFactory:
    def __init__(self, tenant_id: str, backend_client_id: str, uami_client_id: str) -> None:
        self.tenant_id = tenant_id
        self.backend_client_id = backend_client_id
        self.uami_client_id = uami_client_id
        self._mi = _MiAssertionCache(uami_client_id)

    def for_user(self, user_jwt: str) -> OnBehalfOfCredential:
        # `client_assertion_func` is invoked by azure-identity each time a
        # downstream token is fetched; we hand back the cached MI JWT.
        assertion_fn = self._mi.get
        return OnBehalfOfCredential(
            tenant_id=self.tenant_id,
            client_id=self.backend_client_id,
            client_assertion_func=assertion_fn,
            user_assertion=user_jwt,
        )
