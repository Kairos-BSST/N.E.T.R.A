"""
config.py
---------
Central configuration for the N.E.T.R.A cloud video-fetch module.

All settings are loaded from environment variables (or a local .env file)
so that credentials and bucket names are never hard-coded into source code.

Required environment variables:
    GCS_BUCKET_NAME              -> Name of the GCS bucket holding uploaded videos
    GOOGLE_APPLICATION_CREDENTIALS -> Path to your GCP service-account JSON key file

Optional environment variables (sensible defaults provided):
    GCS_VIDEO_PREFIX             -> Only fetch objects under this "folder" prefix (default: "")
    LOCAL_DOWNLOAD_DIR           -> Where downloaded videos are stored locally
    POLL_INTERVAL_SECONDS        -> How often to check the bucket for new videos
    STATE_FILE_PATH              -> Where we track which videos have already been fetched
    MAX_DOWNLOAD_RETRIES         -> Retry attempts for a failed download
    ALLOWED_VIDEO_EXTENSIONS     -> Comma-separated list of extensions to treat as video files
"""

import os
from dotenv import load_dotenv

# Load variables from a .env file if present (harmless if it isn't)
load_dotenv()


class Config:
    GCS_BUCKET_NAME: str = os.getenv("GCS_BUCKET_NAME", "")
    GCS_VIDEO_PREFIX: str = os.getenv("GCS_VIDEO_PREFIX", "")

    GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

    LOCAL_DOWNLOAD_DIR: str = os.getenv("LOCAL_DOWNLOAD_DIR", "./downloaded_videos")
    STATE_FILE_PATH: str = os.getenv("STATE_FILE_PATH", "./fetch_state.json")

    POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
    MAX_DOWNLOAD_RETRIES: int = int(os.getenv("MAX_DOWNLOAD_RETRIES", "3"))

    ALLOWED_VIDEO_EXTENSIONS: tuple = tuple(
        ext.strip().lower()
        for ext in os.getenv(
            "ALLOWED_VIDEO_EXTENSIONS", ".mp4,.avi,.mov,.mkv,.ts"
        ).split(",")
    )

    @classmethod
    def validate(cls) -> None:
        """Fail fast and loudly if required config is missing."""
        missing = []
        if not cls.GCS_BUCKET_NAME:
            missing.append("GCS_BUCKET_NAME")
        if not cls.GOOGLE_APPLICATION_CREDENTIALS:
            missing.append("GOOGLE_APPLICATION_CREDENTIALS")

        if missing:
            raise EnvironmentError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                f"Set them in your environment or in a .env file. See README.md."
            )

        if not os.path.isfile(cls.GOOGLE_APPLICATION_CREDENTIALS):
            raise FileNotFoundError(
                f"GOOGLE_APPLICATION_CREDENTIALS points to a file that does not exist: "
                f"{cls.GOOGLE_APPLICATION_CREDENTIALS}"
            )

        os.makedirs(cls.LOCAL_DOWNLOAD_DIR, exist_ok=True)