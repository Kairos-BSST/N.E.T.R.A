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
        os.replace(tmp_path, self.state_file_path)  

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