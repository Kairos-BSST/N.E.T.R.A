"""
CloudSource — frames from a cloud-fetched local copy (e.g. Google Drive).

After Drive / cloud download lands on disk, wrap the path so the AI
pipeline consumes frames through the same VideoSource interface.
"""

from __future__ import annotations

from typing import Optional

from video_sources.file_source import FileSource


class CloudSource(FileSource):
    """Identical frame API to FileSource; labeled as a cloud-origin feed."""

    source_kind = "cloud"

    def __init__(
        self,
        path: str,
        *,
        remote_id: Optional[str] = None,
        remote_name: Optional[str] = None,
        loop: bool = False,
    ):
        super().__init__(path, loop=loop)
        self.remote_id = remote_id
        self.remote_name = remote_name

    @property
    def label(self) -> str:
        if self.remote_name:
            return f"cloud:{self.remote_name}"
        return f"cloud:{super().label}"
