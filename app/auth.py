import asyncio

import jwt
from jwt import PyJWKClient
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from app.config import settings


class JWKSJWTVerifier(TokenVerifier):
    def __init__(
        self,
        *,
        issuer: str,
        jwks_url: str,
        resource: str,
        algorithms: list[str],
    ) -> None:
        self.issuer = issuer
        self.resource = resource
        self.algorithms = algorithms
        self.jwks = PyJWKClient(jwks_url, cache_keys=True)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = await asyncio.to_thread(
                self.jwks.get_signing_key_from_jwt,
                token,
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.algorithms,
                audience=self.resource,
                issuer=self.issuer,
                options={"require": ["exp", "sub"]},
            )
        except Exception:
            return None

        subject = str(claims["sub"])
        client_id = str(
            claims.get("azp")
            or claims.get("client_id")
            or subject
        )

        scopes: list[str] = []
        scope_claim = claims.get("scope")
        if isinstance(scope_claim, str):
            scopes.extend(part for part in scope_claim.split() if part)
        elif isinstance(scope_claim, list):
            scopes.extend(str(part) for part in scope_claim)

        permissions = claims.get("permissions")
        if isinstance(permissions, list):
            for permission in permissions:
                value = str(permission)
                if value not in scopes:
                    scopes.append(value)

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.resource,
            subject=subject,
            claims=dict(claims),
        )


def mcp_auth_kwargs() -> dict:
    if not settings.mcp_auth_enabled:
        return {}

    missing = [
        name
        for name, value in {
            "OAUTH_ISSUER_URL": settings.oauth_issuer_url,
            "OAUTH_JWKS_URL": settings.oauth_jwks_url,
            "MCP_RESOURCE_URL": settings.mcp_resource_url,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            "OAuth is enabled but required settings are missing: "
            + ", ".join(missing)
        )

    assert settings.oauth_issuer_url
    assert settings.oauth_jwks_url
    assert settings.mcp_resource_url

    algorithms = [
        value.strip()
        for value in settings.oauth_algorithms.split(",")
        if value.strip()
    ]
    if not algorithms:
        raise RuntimeError("OAUTH_ALGORITHMS must contain at least one algorithm")

    scopes = [
        value.strip()
        for value in settings.oauth_required_scopes.split(",")
        if value.strip()
    ]

    verifier = JWKSJWTVerifier(
        issuer=settings.oauth_issuer_url,
        jwks_url=settings.oauth_jwks_url,
        resource=settings.mcp_resource_url,
        algorithms=algorithms,
    )

    return {
        "token_verifier": verifier,
        "auth": AuthSettings(
            issuer_url=AnyHttpUrl(settings.oauth_issuer_url),
            resource_server_url=AnyHttpUrl(settings.mcp_resource_url),
            required_scopes=scopes,
            validate_token_resource=True,
        ),
    }


def current_subject() -> str | None:
    if not settings.mcp_auth_enabled:
        return None

    token = get_access_token()
    if token is None or not token.subject:
        raise PermissionError("Authenticated user identity is unavailable")
    return str(token.subject)
