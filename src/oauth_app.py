"""
OAuth2 Authorization Server for paprika-mcp, human login delegated to Google.

Mirrors the shared MCP standard (OAuth2 everywhere, Google OIDC at /authorize,
RFC 9728 metadata, persistent tokens, identity for auditing). The MCP server is
the OAuth AS to the MCP client; the human login is brokered to Google and
allow-listed by email — no passwords stored.

Public-repo note: this file ships no deployment specifics. The issuer URL,
Google credentials and the email allowlist all come from environment variables
(.env, which is gitignored). Defaults here are generic localhost values.

Deployment model: served under OAUTH_PATH_PREFIX (e.g. /paprika); nginx strips
the prefix (rewrite ^/paprika(/.*)$ $1) so this server serves at root paths.
issuer = OAUTH_ISSUER_URL + "/" + OAUTH_PATH_PREFIX.

Used by: src/server.py (--http mode).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from dotenv import load_dotenv
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.routes import (
    MetadataHandler,
    build_metadata,
    cors_middleware,
    create_auth_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

logger = logging.getLogger("paprika-mcp.oauth")

# This module reads its config at import time; load .env defensively.
load_dotenv()

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "oauth_state.json"

_ISSUER_ROOT = os.getenv("OAUTH_ISSUER_URL", "http://localhost:8000").rstrip("/")
PREFIX = os.getenv("OAUTH_PATH_PREFIX", "paprika").strip("/")
ISSUER = f"{_ISSUER_ROOT}/{PREFIX}" if PREFIX else _ISSUER_ROOT

MCP_PATH = "/mcp"  # backend path (nginx strips the public /paprika prefix)
RESOURCE_URL = f"{ISSUER}/mcp"
PRM_URL = f"{ISSUER}/.well-known/oauth-protected-resource"
CALLBACK_PUBLIC = f"{ISSUER}/oauth/google/callback"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
ALLOWED_EMAILS = {e.strip().lower() for e in os.getenv("OAUTH_ALLOWED_EMAILS", "").split(",") if e.strip()}
TOKEN_TTL = 365 * 24 * 3600

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

_identity: ContextVar[tuple[str | None, str | None]] = ContextVar("identity", default=(None, None))


def set_identity(user: str | None, client: str | None) -> None:
    _identity.set((user, client))


def current_identity() -> tuple[str | None, str | None]:
    return _identity.get()


class PersistentOAuthProvider(OAuthAuthorizationServerProvider):
    """In-process OAuth AS whose clients/tokens survive restarts via JSON."""

    def __init__(self, state_path: Path = STATE_PATH) -> None:
        self._path = state_path
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        self._email_by_code: dict[str, str] = {}
        self._email_by_token: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            self._clients = {k: OAuthClientInformationFull(**v) for k, v in raw.get("clients", {}).items()}
            self._access = {k: AccessToken(**v) for k, v in raw.get("access", {}).items()}
            self._refresh = {k: RefreshToken(**v) for k, v in raw.get("refresh", {}).items()}
            self._email_by_token = raw.get("email_by_token", {})
            logger.info("Loaded OAuth state: %d clients, %d tokens", len(self._clients), len(self._access))
        except Exception as e:
            logger.error("Failed to load OAuth state (%s); starting fresh", e)

    def _save(self) -> None:
        data = {
            "clients": {k: json.loads(v.model_dump_json()) for k, v in self._clients.items()},
            "access": {k: json.loads(v.model_dump_json()) for k, v in self._access.items()},
            "refresh": {k: json.loads(v.model_dump_json()) for k, v in self._refresh.items()},
            "email_by_token": self._email_by_token,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data))
        self._path.chmod(0o600)

    def email_for_token(self, token: str) -> str | None:
        return self._email_by_token.get(token)

    def client_name(self, client_id: str) -> str | None:
        c = self._clients.get(client_id)
        return getattr(c, "client_name", None) if c else None

    async def get_client(self, client_id: str):
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info
        self._save()
        logger.info("Registered OAuth client %s (%s)", client_info.client_id,
                    getattr(client_info, "client_name", None))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + 300,
            client_id=client.client_id, code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, authorization_code):
        c = self._codes.get(authorization_code)
        if c and c.client_id == client.client_id and c.expires_at > time.time():
            return c
        return None

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        email = self._email_by_code.pop(authorization_code.code, None)
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._access[at] = AccessToken(token=at, client_id=client.client_id,
                                       scopes=authorization_code.scopes,
                                       expires_at=int(time.time()) + TOKEN_TTL)
        self._refresh[rt] = RefreshToken(token=rt, client_id=client.client_id,
                                         scopes=authorization_code.scopes)
        if email:
            self._email_by_token[at] = email
            self._email_by_token[rt] = email
        self._save()
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=TOKEN_TTL,
                          refresh_token=rt, scope=" ".join(authorization_code.scopes) or None)

    async def load_refresh_token(self, client, refresh_token):
        rt = self._refresh.get(refresh_token)
        return rt if rt and rt.client_id == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token, scopes) -> OAuthToken:
        email = self._email_by_token.get(refresh_token.token)
        self._refresh.pop(refresh_token.token, None)
        for t in [t for t, a in self._access.items() if a.client_id == client.client_id]:
            self._access.pop(t, None)
            self._email_by_token.pop(t, None)
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        eff = scopes or refresh_token.scopes
        self._access[at] = AccessToken(token=at, client_id=client.client_id, scopes=eff,
                                       expires_at=int(time.time()) + TOKEN_TTL)
        self._refresh[rt] = RefreshToken(token=rt, client_id=client.client_id, scopes=eff)
        if email:
            self._email_by_token[at] = email
            self._email_by_token[rt] = email
        self._save()
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=TOKEN_TTL,
                          refresh_token=rt, scope=" ".join(eff) or None)

    async def load_access_token(self, token: str):
        a = self._access.get(token)
        if a and (a.expires_at is None or a.expires_at > time.time()):
            return a
        return None

    async def revoke_token(self, token) -> None:
        if isinstance(token, AccessToken):
            self._access.pop(token.token, None)
        else:
            self._refresh.pop(token.token, None)
        self._email_by_token.pop(token.token, None)
        self._save()

    def attach_email_to_code(self, code: str, email: str) -> None:
        if code:
            self._email_by_code[code] = email


def _google_routes(provider: PersistentOAuthProvider) -> list[Route]:
    pending: dict[str, dict] = {}

    async def authorize(request: Request):
        if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and ALLOWED_EMAILS):
            return JSONResponse({"error": "server_misconfigured"}, status_code=500)
        login_state = secrets.token_urlsafe(24)
        pending[login_state] = dict(request.query_params)
        params = {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": CALLBACK_PUBLIC,
            "response_type": "code",
            "scope": "openid email",
            "state": login_state,
            "access_type": "online",
            "prompt": "select_account",
        }
        return RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}", status_code=302)

    async def callback(request: Request):
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        orig = pending.pop(state, None)
        if orig is None or not code:
            return JSONResponse({"error": "invalid_state"}, status_code=400)
        async with httpx.AsyncClient(timeout=10) as http:
            tok = await http.post(GOOGLE_TOKEN, data={
                "code": code, "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": CALLBACK_PUBLIC, "grant_type": "authorization_code",
            })
            if tok.status_code != 200:
                logger.warning("Google token exchange failed: %s", tok.text[:200])
                return JSONResponse({"error": "google_token_failed"}, status_code=502)
            ui = await http.get(GOOGLE_USERINFO, headers={
                "Authorization": f"Bearer {tok.json().get('access_token')}"})
        email = (ui.json().get("email") or "").lower() if ui.status_code == 200 else ""
        if not email or email not in ALLOWED_EMAILS:
            logger.warning("Denied login for email=%r", email)
            return JSONResponse({"error": "access_denied", "email": email}, status_code=403)

        p = dict(orig)
        client = await provider.get_client(p.get("client_id", ""))
        if client is None:
            return JSONResponse({"error": "invalid_client"}, status_code=400)
        ap = AuthorizationParams(
            state=p.get("state"),
            scopes=(p.get("scope") or "").split() or None,
            code_challenge=p["code_challenge"],
            redirect_uri=AnyHttpUrl(p["redirect_uri"]),
            redirect_uri_provided_explicitly="redirect_uri" in p,
            resource=p.get("resource"),
        )
        redirect = await provider.authorize(client, ap)
        issued = parse_qs(redirect.split("?", 1)[1]).get("code", [""])[0]
        provider.attach_email_to_code(issued, email)
        logger.info("Login OK for %s (client %s)", email, client.client_id)
        return RedirectResponse(redirect, status_code=302)

    return [
        Route("/authorize", authorize, methods=["GET"]),
        Route("/oauth/google/callback", callback, methods=["GET"]),
    ]


def build_oauth_app(provider: PersistentOAuthProvider) -> Starlette:
    """Starlette app serving OAuth + discovery endpoints (at root; nginx strips
    the public /<prefix>)."""
    sdk_routes = create_auth_routes(
        provider=provider,
        issuer_url=AnyHttpUrl(ISSUER),
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    sdk_routes = [r for r in sdk_routes if getattr(r, "path", None) != "/authorize"]

    prm = ProtectedResourceMetadata(
        resource=AnyHttpUrl(RESOURCE_URL),
        authorization_servers=[AnyHttpUrl(ISSUER)],
        resource_name="paprika-mcp",
    )
    prm_route = Route(
        "/.well-known/oauth-protected-resource",
        endpoint=cors_middleware(ProtectedResourceMetadataHandler(prm).handle, ["GET", "OPTIONS"]),
        methods=["GET", "OPTIONS"],
    )

    # Some clients (e.g. claude.ai) discover via OIDC's openid-configuration
    # rather than oauth-authorization-server. Serve the same AS metadata there.
    metadata = build_metadata(
        AnyHttpUrl(ISSUER), None,
        ClientRegistrationOptions(enabled=True), RevocationOptions(),
    )
    oidc_route = Route(
        "/.well-known/openid-configuration",
        endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
        methods=["GET", "OPTIONS"],
    )
    return Starlette(routes=[*_google_routes(provider), prm_route, oidc_route, *sdk_routes])
