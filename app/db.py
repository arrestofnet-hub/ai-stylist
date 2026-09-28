import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                age_range TEXT,
                gender TEXT,
                height_cm INTEGER,
                weight_kg REAL,
                style_goal TEXT,
                preferences_json TEXT NOT NULL DEFAULT '{}',
                free_tries INTEGER NOT NULL DEFAULT 3,
                paid_credits INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reference_photos (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                original_name TEXT,
                mime_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            """
        )


def decode_preferences(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
