"""
drive_auth.py
--------------
Handles the Google OAuth2 consent flow for Drive access, and keeps track
of which browser session has which credentials.

Flow:
    1. Frontend hits GET /auth/google/login
    2. We build a Google authorization URL and redirect the browser to it
    3. User logs in and approves access on Google's own page
    4. Google redirects back to GET /auth/google/callback?code=...&state=...
    5. We exchange the code for credentials (access + refresh token)
    6. Credentials are stored in SESSION_STORE, keyed by a session cookie
    7. Every subsequent /drive/* request reads that cookie to find its
       credentials

SESSION_STORE is a plain in-memory dict here, which is fine for local
development and a single-instance deployment. If you deploy this behind
multiple server processes or need sessions to survive a restart, swap
this for Redis or a database table with the same three functions.
"""

import logging
import secrets
from typing import Optional

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

from cloud_integration.config import Config

logger = logging.getLogger("netra.drive_auth")

# session_id -> {"credentials": Credentials, ...}
SESSION_STORE: dict = {}

# state -> True, used only to check the OAuth "state" param matches what we issued
# (CSRF protection for the OAuth redirect). Short-lived; cleared once used.
_PENDING_STATES: dict = {}


def build_flow() -> Flow:
    Config.validate_drive()
    flow = Flow.from_client_secrets_file(
        Config.GOOGLE_OAUTH_CLIENT_SECRETS_FILE,
        scopes=list(Config.DRIVE_SCOPES),
        redirect_uri=Config.GOOGLE_OAUTH_REDIRECT_URI,
    )
    return flow


def get_authorization_url() -> tuple:
    """Returns (auth_url, state). Caller should store `state` to verify on callback."""
    flow = build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",       # request a refresh token
        include_granted_scopes="true",
        prompt="consent",
    )
    _PENDING_STATES[state] = True
    return auth_url, state


def exchange_code_for_credentials(code: str, state: str) -> Credentials:
    """Exchange an authorization code for real credentials. Raises if state is unrecognized."""
    if state not in _PENDING_STATES:
        raise ValueError("Unrecognized or expired OAuth state - possible CSRF attempt or stale link.")
    _PENDING_STATES.pop(state, None)

    flow = build_flow()
    flow.fetch_token(code=code)
    return flow.credentials


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def store_credentials(session_id: str, credentials: Credentials) -> None:
    SESSION_STORE[session_id] = credentials
    logger.info("Stored Drive credentials for session %s...", session_id[:8])


def get_credentials(session_id: Optional[str]) -> Optional[Credentials]:
    if not session_id:
        return None
    creds = SESSION_STORE.get(session_id)
    if creds is None:
        return None

    # Refresh expired access tokens transparently using the stored refresh token
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        creds.refresh(GoogleAuthRequest())
        SESSION_STORE[session_id] = creds

    return creds


def is_connected(session_id: Optional[str]) -> bool:
    return get_credentials(session_id) is not None