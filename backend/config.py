"""
config.py
---------
Central configuration for the N.E.T.R.A Signal Intake backend.

Everything is loaded from environment variables (or a local .env file) so
credentials are never hard-coded into source code.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))       # .../N.E.T.R.A/backend
    PROJECT_ROOT = os.path.dirname(BASE_DIR)                     # .../N.E.T.R.A

    GOOGLE_OAUTH_CLIENT_SECRETS_FILE: str = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRETS_FILE",
    os.path.join(PROJECT_ROOT, "cloud_integration", "google_oauth_client_secret.json")
)
    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )
    DRIVE_SCOPES: tuple = ("https://www.googleapis.com/auth/drive.readonly",)
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "netra_session")

    LOCAL_DRIVE_DOWNLOAD_DIR: str = os.getenv("LOCAL_DRIVE_DOWNLOAD_DIR", "./downloaded_videos/drive")
    LOCAL_UPLOAD_DIR: str = os.getenv("LOCAL_UPLOAD_DIR", "./downloaded_videos/uploads")
    STATE_FILE_PATH: str = os.getenv("STATE_FILE_PATH", "./fetch_state.json")

    # Where per-event evidence thumbnails (snapshots) are written. Served
    # back to the frontend/report via the /snapshots static mount.
    SNAPSHOT_DIR: str = os.getenv("SNAPSHOT_DIR", "./analysis_snapshots")

    # Minimum gap (seconds of video time) between two logged events of the
    # SAME type on the SAME job. Prevents one long detection from spamming
    # a new report row on every processed frame.
    EVENT_DEDUP_SECONDS: float = float(os.getenv("EVENT_DEDUP_SECONDS", "2.0"))

    # Max upload size in bytes (default 2 GB). Override with MAX_UPLOAD_BYTES.
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))

    # Common video containers / codecs. MIME video/* is also accepted.
    ALLOWED_VIDEO_EXTENSIONS: frozenset = frozenset({
        ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v",
        ".mpeg", ".mpg", ".mpe", ".3gp", ".3g2", ".ts", ".mts", ".m2ts",
        ".ogv", ".vob", ".asf", ".f4v", ".mxf", ".rm", ".rmvb", ".divx",
    })

    @classmethod
    def ensure_storage_dirs(cls) -> None:
        os.makedirs(cls.LOCAL_DRIVE_DOWNLOAD_DIR, exist_ok=True)
        os.makedirs(cls.LOCAL_UPLOAD_DIR, exist_ok=True)
        os.makedirs(cls.SNAPSHOT_DIR, exist_ok=True)

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
        cls.ensure_storage_dirs()

print("REDIRECT URI IN USE:", Config.GOOGLE_OAUTH_REDIRECT_URI)