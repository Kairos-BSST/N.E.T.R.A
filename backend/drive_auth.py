import logging
import secrets
from typing import Optional

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

from config import Config

logger = logging.getLogger("netra.drive_auth")
SESSION_STORE: dict = {}
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
    _PENDING_STATES[state] = flow.code_verifier
    return auth_url, state

def exchange_code_for_credentials(code: str, state: str) -> Credentials:
    if state not in _PENDING_STATES:
        raise ValueError("Unrecognized or expired OAuth state - possible CSRF attempt or stale link.")
    code_verifier = _PENDING_STATES.pop(state, None)

    flow = build_flow()
    flow.code_verifier = code_verifier
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