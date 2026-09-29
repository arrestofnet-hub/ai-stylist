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
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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

            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                tier TEXT NOT NULL,
                instruction TEXT NOT NULL,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                quality TEXT NOT NULL,
                size TEXT NOT NULL,
                status TEXT NOT NULL,
                output_path TEXT,
                base_generation_id TEXT,
                cost_credits INTEGER NOT NULL DEFAULT 1,
                charged_free INTEGER NOT NULL DEFAULT 0,
                charged_paid INTEGER NOT NULL DEFAULT 0,
                usage_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(base_generation_id) REFERENCES generations(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reference_photos_profile
                ON reference_photos(profile_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_generations_profile
                ON generations(profile_id, created_at);
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
