"""
SQLite database models for the posting queue.

Schema:
  queue    - one row per scheduled post (album endcard)
  rotation - singleton row tracking which album is next
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

# Default database path
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "outputs", "queue.db")


@contextmanager
def get_db(db_path=None):
    """Context manager for database connections."""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    """Create tables if they don't exist."""
    with get_db(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                album_slug          TEXT NOT NULL,
                album_name          TEXT NOT NULL,
                artist              TEXT NOT NULL,
                scheduled_date      TEXT NOT NULL UNIQUE,
                video_path          TEXT NOT NULL,
                catbox_url          TEXT,
                caption             TEXT NOT NULL DEFAULT '',
                status              TEXT NOT NULL DEFAULT 'pending',
                instagram_media_id  TEXT,
                created_at          TEXT DEFAULT (datetime('now')),
                posted_at           TEXT,
                error_message       TEXT
            );

            CREATE TABLE IF NOT EXISTS rotation (
                id                  INTEGER PRIMARY KEY CHECK (id = 1),
                next_album_index    INTEGER NOT NULL DEFAULT 0
            );

            INSERT OR IGNORE INTO rotation (id, next_album_index) VALUES (1, 0);
        """)
    print("Database initialized.")


# --- Queue CRUD ---

def get_all_queue(db_path=None):
    """Return all queue entries ordered by scheduled date."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM queue ORDER BY scheduled_date ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_queue_entry(entry_id, db_path=None):
    """Return a single queue entry by ID."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM queue WHERE id = ?", (entry_id,)
        ).fetchone()
    return dict(row) if row else None


def get_pending_count(db_path=None):
    """Count entries by status."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM queue GROUP BY status"
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


def get_buffer_days(db_path=None):
    """How many future days have queued (non-rejected) entries."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM queue "
            "WHERE scheduled_date >= ? AND status != 'rejected'",
            (today,)
        ).fetchone()
    return row["cnt"] if row else 0


def has_entry_for_date(date_str, db_path=None):
    """Check if a date already has a queue entry."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM queue WHERE scheduled_date = ?", (date_str,)
        ).fetchone()
    return row is not None


def is_album_queued_or_posted(album_slug, db_path=None):
    """Check if an album is already queued or posted (not rejected)."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM queue WHERE album_slug = ? AND status != 'rejected'",
            (album_slug,)
        ).fetchone()
    return row is not None


def insert_queue_entry(album_slug, album_name, artist, scheduled_date,
                       video_path, caption, db_path=None):
    """Insert a new queue entry with status 'pending'."""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO queue (album_slug, album_name, artist, scheduled_date, "
            "video_path, caption, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (album_slug, album_name, artist, scheduled_date, video_path, caption)
        )
    print(f"  Queued: {album_name} for {scheduled_date}")


def update_status(entry_id, status, db_path=None, **kwargs):
    """Update the status of a queue entry. Extra kwargs set additional columns."""
    sets = ["status = ?"]
    params = [status]

    if status == "posted":
        sets.append("posted_at = datetime('now')")

    for key, value in kwargs.items():
        if key in ("catbox_url", "instagram_media_id", "error_message", "caption"):
            sets.append(f"{key} = ?")
            params.append(value)

    params.append(entry_id)

    with get_db(db_path) as conn:
        conn.execute(
            f"UPDATE queue SET {', '.join(sets)} WHERE id = ?",
            params
        )


def update_caption(entry_id, caption, db_path=None):
    """Update the caption for a queue entry."""
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE queue SET caption = ? WHERE id = ?",
            (caption, entry_id)
        )


# --- Rotation ---

def get_rotation_index(db_path=None):
    """Get the current rotation index."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT next_album_index FROM rotation WHERE id = 1"
        ).fetchone()
    return row["next_album_index"] if row else 0


def advance_rotation(db_path=None):
    """Increment the rotation index by 1."""
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE rotation SET next_album_index = next_album_index + 1 WHERE id = 1"
        )
