import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))       
    PROJECT_ROOT = os.path.dirname(BASE_DIR)                     
    GOOGLE_OAUTH_CLIENT_SECRETS_FILE: str = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRETS_FILE",
    os.path.join(PROJECT_ROOT, "cloud_integration", "google_oauth_client_secret.json")
)
    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )
    DRIVE_SCOPES: tuple = ("https://www.googleapis.com/auth/drive.readonly",)
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "netra_session")
    SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", str(12 * 60 * 60)))
    DATABASE_PATH: str = os.getenv("NETRA_DATABASE_PATH", os.path.join(BASE_DIR, "netra.db"))
    ADMIN_USERNAME: str = os.getenv("NETRA_ADMIN_USERNAME")
    ADMIN_PASSWORD: str = os.getenv("NETRA_ADMIN_PASSWORD")
    OPERATOR_USERNAME: str = os.getenv("NETRA_OPERATOR_USERNAME")
    OPERATOR_PASSWORD: str = os.getenv("NETRA_OPERATOR_PASSWORD")

    LOCAL_DRIVE_DOWNLOAD_DIR: str = os.getenv("LOCAL_DRIVE_DOWNLOAD_DIR", "./downloaded_videos/drive")
    LOCAL_UPLOAD_DIR: str = os.getenv("LOCAL_UPLOAD_DIR", "./downloaded_videos/uploads")
    STATE_FILE_PATH: str = os.getenv("STATE_FILE_PATH", "./fetch_state.json")
    # Where per-event evidence thumbnails (snapshots) are written.
    SNAPSHOT_DIR: str = os.getenv("SNAPSHOT_DIR", "./analysis_snapshots")
    # Enrolled person-of-interest face images (max 2 per POI).
    POI_GALLERY_DIR: str = os.getenv("POI_GALLERY_DIR", "./poi_gallery")

    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    EVENT_DEDUP_SECONDS: float = float(os.getenv("EVENT_DEDUP_SECONDS", "2.0"))
    VIDEO_ANALYSIS_FPS: float = float(os.getenv("NETRA_ANALYSIS_FPS", "8"))

    # Max upload size in bytes (default 2 GB)
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
        os.makedirs(cls.POI_GALLERY_DIR, exist_ok=True)

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