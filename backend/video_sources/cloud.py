from __future__ import annotations
from typing import Optional
from video_sources.file_source import FileSource

class CloudSource(FileSource):
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