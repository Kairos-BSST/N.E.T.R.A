import os
import re
from typing import Optional
from config import Config

def safe_filename(name: str) -> str:
    """Strip path components and keep a filesystem-safe basename."""
    base = os.path.basename(name or "").strip() or "video"
    base = re.sub(r"[^\w.\- ()\[\]]+", "_", base)
    return base[:200] or "video"

def is_allowed_video(filename: str, content_type: Optional[str]) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    if ext in Config.ALLOWED_VIDEO_EXTENSIONS:
        return True
    if content_type and content_type.lower().startswith("video/"):
        return True
    return False
