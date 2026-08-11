"""Shared runtime dependencies."""
from config import Config
from state_tracker import StateTracker
import database

state = StateTracker(Config.STATE_FILE_PATH)
Config.ensure_storage_dirs()
database.init_db()
