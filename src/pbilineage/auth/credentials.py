"""Service-principal tokens for the Power BI REST APIs.

One Azure AD app registration backs both surfaces this tool uses, but they
are permissioned separately and it is worth being explicit about which is
which:

* **Admin REST APIs** (Scanner, Export, GetModifiedWorkspaces) need the
  `Tenant.Read.All` application permission *and* the tenant setting
  "Service principals can use read-only Power BI admin APIs" turned on, with
  the app's security group added to it.
* **XMLA endpoint** needs the service principal to be a member (Viewer is
  enough for read) of each workspace, and the workspace must be on capacity.

MSAL is used when it is installed; otherwise the client-credentials flow is
performed directly against the token endpoint, so the package works with
nothing but the standard library.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from typing import Protocol

from pbilineage.clients.http import (
    ApiError,
    RetryPolicy,
    Transport,
    UrllibTransport,
    request_with_retry,
)
from pbilineage.config import POWERBI_SCOPE, Settings

__all__ = ["AccessToken", "ClientCredentialProvider", "StaticTokenProvider", "TokenProvider"]

#: refresh this many seconds before actual expiry
EXPIRY_SKEW_SECONDS = 300


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - EXPIRY_SKEW_SECONDS

    def header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class TokenProvider(Protocol):
    def get_token(self) -> AccessToken: ...


@dataclass(slots=True)
class StaticTokenProvider:
    """For tests and for environments that inject a token out of band."""

    value: str = "test-token"
    lifetime_seconds: float = 3600.0

    def get_token(self) -> AccessToken:
        return AccessToken(self.value, time.time() + self.lifetime_seconds)


class ClientCredentialProvider:
    """Client-credentials token acquisition, with in-process caching."""

    def __init__(
        self,
        settings: Settings,
        transport: Transport | None = None,
        policy: RetryPolicy | None = None,
        scope: str = POWERBI_SCOPE,
    ) -> None:
        if not settings.has_credentials:
            missing = ", ".join(settings.missing())
            raise ApiError(f"missing service-principal configuration: {missing}")
        self._settings = settings
        self._transport = transport or UrllibTransport()
        self._policy = policy or RetryPolicy(max_attempts=settings.max_retries)
        self._scope = scope
        self._cached: AccessToken | None = None

    @property
    def authority(self) -> str:
        return f"{self._settings.authority}/{self._settings.tenant_id}"

    def get_token(self) -> AccessToken:
        if self._cached is not None and not self._cached.expired:
            return self._cached
        self._cached = self._acquire_msal() or self._acquire_direct()
        return self._cached

    def _acquire_msal(self) -> AccessToken | None:
        try:
            import msal  # type: ignore[import-not-found]
        except ImportError:
            return None

        credential: object
        if self._settings.certificate_path:
            with open(self._settings.certificate_path, "r", encoding="utf-8") as handle:
                credential = {
                    "private_key": handle.read(),
                    "thumbprint": self._settings.certificate_thumbprint,
                }
        else:
            credential = self._settings.client_secret

        app = msal.ConfidentialClientApplication(
            client_id=self._settings.client_id,
            authority=self.authority,
            client_credential=credential,
        )
        result = app.acquire_token_for_client(scopes=[self._scope])
        if not isinstance(result, dict) or "access_token" not in result:
            description = ""
            if isinstance(result, dict):
                description = str(result.get("error_description") or result.get("error") or "")
            raise ApiError(f"Azure AD refused the client credentials: {description}")
        return AccessToken(
            token=str(result["access_token"]),
            expires_at=time.time() + float(result.get("expires_in", 3599)),
        )

    def _acquire_direct(self) -> AccessToken:
        """OAuth2 client-credentials against the v2.0 token endpoint."""
        if not self._settings.client_secret:
            raise ApiError(
                "certificate authentication requires the 'msal' package; "
                "install pbilineage[live] or set PBI_CLIENT_SECRET"
            )
        url = f"{self.authority}/oauth2/v2.0/token"
        payload = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._settings.client_id,
                "client_secret": self._settings.client_secret,
                "scope": self._scope,
            }
        ).encode("utf-8")
        response = request_with_retry(
            self._transport,
            self._policy,
            "POST",
            url,
            {"Content-Type": "application/x-www-form-urlencoded"},
            payload,
            timeout=self._settings.request_timeout_seconds,
        )
        if not response.ok:
            raise ApiError(
                "Azure AD token request failed",
                status=response.status,
                body=response.body.decode("utf-8", errors="replace")[:500],
            )
        payload_json = response.json() or {}
        token = payload_json.get("access_token")
        if not token:
            raise ApiError("Azure AD response contained no access_token", response.status)
        return AccessToken(
            token=str(token),
            expires_at=time.time() + float(payload_json.get("expires_in", 3599)),
        )
