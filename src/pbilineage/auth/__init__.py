"""Azure AD authentication for the service principal."""

from __future__ import annotations

from pbilineage.auth.credentials import AccessToken, StaticTokenProvider, TokenProvider

__all__ = ["AccessToken", "StaticTokenProvider", "TokenProvider"]
