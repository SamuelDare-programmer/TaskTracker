"""
config.py — Application Settings
─────────────────────────────────────────────────────────────────────────────
This module uses pydantic-settings to manage all configuration in one place.

HOW IT WORKS:
  1. We define a Settings class with typed fields and default values.
  2. pydantic-settings automatically reads values from environment variables
     OR from a .env file (in that priority order).
  3. Any code that needs a setting just imports `settings` from here.

WHY THIS PATTERN?
  - Single source of truth for all config.
  - No hard-coded values scattered around the codebase.
  - Switching between dev/prod is just changing a .env file.
─────────────────────────────────────────────────────────────────────────────
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = True           # Set to False in production

    # ── Database ──────────────────────────────────────────────────────────────
    # Default: SQLite (file-based, no server needed, perfect for learning)
    # For PostgreSQL: "postgresql+asyncpg://user:password@host:5432/dbname"
    DATABASE_URL: str = "sqlite+aiosqlite:///./tasks.db"

    # ── Security ──────────────────────────────────────────────────────────────
    # Used to sign JWTs if you add authentication later. CHANGE THIS in prod!
    SECRET_KEY: str = "super-secret-change-me-in-production"

    # ── Background Worker ─────────────────────────────────────────────────────
    # Controls how many tasks can run at the same time.
    # Think of it like a restaurant: only 5 chefs cooking at once.
    MAX_CONCURRENT_TASKS: int = 5

    # ── Pydantic Settings Config ───────────────────────────────────────────────
    # `env_file` tells pydantic-settings where to find the .env file.
    # `extra="ignore"` means unknown env vars don't cause an error.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Create a single, shared instance of Settings.
# Import this `settings` object everywhere you need config values:
#   from config import settings
#   print(settings.DATABASE_URL)
settings = Settings()