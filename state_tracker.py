"""
state_tracker.py
-----------------
Keeps track of which videos have already been fetched from the cloud so
that the fetcher never downloads the same file twice, even across restarts.

State is persisted as a simple JSON file:
    {
        "camera1/2026-08-01_1200.mp4": {
            "downloaded_at": "2026-08-02T10:15:00",
            "local_path": "./downloaded_videos/camera1/2026-08-01_1200.mp4",
            "size_bytes": 10485760
        },
        ...
    }

For a small-to-medium deployment a JSON file is plenty. If your video
volume grows very large (tens of thousands of files), swap this out for
a SQLite table with the same three methods (has_been_fetched, mark_fetched,
all_fetched) and nothing else in the codebase needs to change.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict


class StateTracker:
    def __init__(self, state_file_path: str):
        self.state_file_path = state_file_path
        self._lock = threading.Lock()
        self._state: Dict[str, dict] = self._load()

    def _load(self) -> Dict[str, dict]:
        if os.path.isfile(self.state_file_path):
            try:
                with open(self.state_file_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable state file -> start fresh rather than crash
                return {}
        return {}

    def _save(self) -> None:
        tmp_path = f"{self.state_file_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp_path, self.state_file_path)  # atomic on POSIX

    def has_been_fetched(self, blob_name: str) -> bool:
        with self._lock:
            return blob_name in self._state

    def mark_fetched(self, blob_name: str, local_path: str, size_bytes: int) -> None:
        with self._lock:
            self._state[blob_name] = {
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "local_path": local_path,
                "size_bytes": size_bytes,
            }
            self._save()

    def all_fetched(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._state)