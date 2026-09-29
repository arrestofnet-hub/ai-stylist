import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
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


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    generation_columns = _column_names(conn, "generations")
    if "idempotency_key" not in generation_columns:
        conn.execute("ALTER TABLE generations ADD COLUMN idempotency_key TEXT")
    if "duration_ms" not in generation_columns:
        conn.execute("ALTER TABLE generations ADD COLUMN duration_ms INTEGER")

    profile_columns = _column_names(conn, "profiles")
    if "owner_subject" not in profile_columns:
        conn.execute("ALTER TABLE profiles ADD COLUMN owner_subject TEXT")

    photo_columns = _column_names(conn, "reference_photos")
    if "role" not in photo_columns:
        conn.execute(
            "ALTER TABLE reference_photos ADD COLUMN role TEXT NOT NULL DEFAULT 'other'"
        )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_generations_idempotency
        ON generations(profile_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )


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
                owner_subject TEXT,
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
                role TEXT NOT NULL DEFAULT 'other',
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
                idempotency_key TEXT,
                cost_credits INTEGER NOT NULL DEFAULT 1,
                charged_free INTEGER NOT NULL DEFAULT 0,
                charged_paid INTEGER NOT NULL DEFAULT 0,
                usage_json TEXT,
                duration_ms INTEGER,
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(base_generation_id) REFERENCES generations(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS credit_transactions (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL,
                reason TEXT,
                external_reference TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_profiles_owner
                ON profiles(owner_subject);

            CREATE INDEX IF NOT EXISTS idx_reference_photos_profile
                ON reference_photos(profile_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_generations_profile
                ON generations(profile_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_credit_transactions_profile
                ON credit_transactions(profile_id, created_at);
            """
        )
        _apply_migrations(conn)


def decode_preferences(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
