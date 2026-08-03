"""
config.py
---------
Central configuration for the N.E.T.R.A Drive integration.

Everything is loaded from environment variables (or a local .env file) so
credentials are never hard-coded into source code.

Required:
    GOOGLE_OAUTH_CLIENT_SECRETS_FILE -> Path to your OAuth client JSON
                                         (Google Cloud Console -> Credentials)

Optional (sensible defaults provided):
    GOOGLE_OAUTH_REDIRECT_URI -> Must exactly match what you registered in
                                 Google Cloud Console (default assumes local dev)
    LOCAL_DRIVE_DOWNLOAD_DIR  -> Where fetched Drive videos are saved locally
    STATE_FILE_PATH           -> Tracks which Drive files have already been fetched
    SESSION_COOKIE_NAME       -> Name of the cookie storing the OAuth session id
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    GOOGLE_OAUTH_CLIENT_SECRETS_FILE: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", "")
    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )
    DRIVE_SCOPES: tuple = ("https://www.googleapis.com/auth/drive.readonly",)
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "netra_session")

    LOCAL_DRIVE_DOWNLOAD_DIR: str = os.getenv("LOCAL_DRIVE_DOWNLOAD_DIR", "./downloaded_videos/drive")
    STATE_FILE_PATH: str = os.getenv("STATE_FILE_PATH", "./fetch_state.json")

    @classmethod
    def validate_drive(cls) -> None:
        """Fail fast and loudly if required Drive OAuth config is missing."""
        if not cls.GOOGLE_OAUTH_CLIENT_SECRETS_FILE:
            raise EnvironmentError(
                "Missing GOOGLE_OAUTH_CLIENT_SECRETS_FILE. Download an OAuth 2.0 "
                "Client ID (Web application type) from Google Cloud Console and "
                "point this at the downloaded JSON file. See README."
            )
        if not os.path.isfile(cls.GOOGLE_OAUTH_CLIENT_SECRETS_FILE):
            raise FileNotFoundError(
                f"GOOGLE_OAUTH_CLIENT_SECRETS_FILE points to a file that does not exist: "
                f"{cls.GOOGLE_OAUTH_CLIENT_SECRETS_FILE}"
            )
        os.makedirs(cls.LOCAL_DRIVE_DOWNLOAD_DIR, exist_ok=True)
        
print("REDIRECT URI IN USE:", Config.GOOGLE_OAUTH_REDIRECT_URI)  