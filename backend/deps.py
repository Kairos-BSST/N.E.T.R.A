"""
deps.py
-------
Shared runtime dependencies for the Signal Intake API.
"""

from config import Config
from state_tracker import StateTracker

state = StateTracker(Config.STATE_FILE_PATH)
Config.ensure_storage_dirs()
