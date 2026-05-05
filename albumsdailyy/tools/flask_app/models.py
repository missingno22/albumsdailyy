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
    """Create tables if they don't exist, then run forward-only migrations."""
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
        _migrate_queue_for_stats(conn)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posted_artist_stats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_key  TEXT NOT NULL,
                stat_type   TEXT NOT NULL,
                queued_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(artist_key, stat_type)
            );
        """)
    print("Database initialized.")


def _migrate_queue_for_stats(conn):
    """Add post_type/post_format/stat_type/scheduled_time and drop scheduled_date UNIQUE.

    Idempotent: detects current schema and only does work that's still needed.
    """
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(queue)").fetchall()}

    # Step 1: add new columns if missing (these can be added in-place)
    if "post_type" not in cols:
        # 'album_review' | 'stats_reel' | 'stats_post'
        conn.execute("ALTER TABLE queue ADD COLUMN post_type TEXT NOT NULL DEFAULT 'album_review'")
        print("  [migrate] added queue.post_type")
    if "post_format" not in cols:
        # 'reel' | 'image'
        conn.execute("ALTER TABLE queue ADD COLUMN post_format TEXT NOT NULL DEFAULT 'reel'")
        print("  [migrate] added queue.post_format")
    if "stat_type" not in cols:
        conn.execute("ALTER TABLE queue ADD COLUMN stat_type TEXT")
        print("  [migrate] added queue.stat_type")
    if "scheduled_time" not in cols:
        # HH:MM, used to order multiple posts on the same date
        conn.execute("ALTER TABLE queue ADD COLUMN scheduled_time TEXT NOT NULL DEFAULT '19:00'")
        print("  [migrate] added queue.scheduled_time")

    # Step 2: drop UNIQUE on scheduled_date by recreating the table.
    # Detect via the table's CREATE SQL.
    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='queue'"
    ).fetchone()
    if create_sql and "scheduled_date      TEXT NOT NULL UNIQUE" in create_sql["sql"]:
        print("  [migrate] dropping UNIQUE(scheduled_date) — rebuilding queue table")
        conn.executescript("""
            CREATE TABLE queue_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                album_slug          TEXT NOT NULL,
                album_name          TEXT NOT NULL,
                artist              TEXT NOT NULL,
                scheduled_date      TEXT NOT NULL,
                scheduled_time      TEXT NOT NULL DEFAULT '19:00',
                video_path          TEXT NOT NULL,
                catbox_url          TEXT,
                caption             TEXT NOT NULL DEFAULT '',
                status              TEXT NOT NULL DEFAULT 'pending',
                instagram_media_id  TEXT,
                created_at          TEXT DEFAULT (datetime('now')),
                posted_at           TEXT,
                error_message       TEXT,
                post_type           TEXT NOT NULL DEFAULT 'album_review',
                post_format         TEXT NOT NULL DEFAULT 'reel',
                stat_type           TEXT
            );
            INSERT INTO queue_new (
                id, album_slug, album_name, artist, scheduled_date, scheduled_time,
                video_path, catbox_url, caption, status, instagram_media_id,
                created_at, posted_at, error_message, post_type, post_format, stat_type
            )
            SELECT
                id, album_slug, album_name, artist, scheduled_date,
                COALESCE(scheduled_time, '19:00'),
                video_path, catbox_url, caption, status, instagram_media_id,
                created_at, posted_at, error_message,
                COALESCE(post_type, 'album_review'),
                COALESCE(post_format, 'reel'),
                stat_type
            FROM queue;
            DROP TABLE queue;
            ALTER TABLE queue_new RENAME TO queue;
            CREATE INDEX idx_queue_sched ON queue(scheduled_date, scheduled_time);
        """)
        print("  [migrate] queue table rebuilt — UNIQUE removed, multi-post-per-day allowed")


# --- Queue CRUD ---

def get_all_queue(db_path=None):
    """Return all queue entries ordered by scheduled date+time."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM queue ORDER BY scheduled_date ASC, scheduled_time ASC"
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


def has_entry_for_date(date_str, db_path=None, post_type=None):
    """Check if a date already has a queue entry. Optionally filter by post_type."""
    with get_db(db_path) as conn:
        if post_type is None:
            row = conn.execute(
                "SELECT id FROM queue WHERE scheduled_date = ?", (date_str,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM queue WHERE scheduled_date = ? AND post_type = ?",
                (date_str, post_type),
            ).fetchone()
    return row is not None


def has_slot_for_date(date_str, scheduled_time, db_path=None):
    """Check if a specific (date, time) slot is taken."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM queue WHERE scheduled_date = ? AND scheduled_time = ?",
            (date_str, scheduled_time),
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
                       video_path, caption, db_path=None,
                       scheduled_time="19:00", post_type="album_review",
                       post_format="reel", stat_type=None):
    """Insert a new queue entry with status 'pending'.

    For album reviews, defaults are fine.
    For artist stats, set post_type='stats_reel'/'stats_post', post_format='reel'/'image',
    and stat_type to one of stats_registry.STAT_TYPES.
    """
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO queue (album_slug, album_name, artist, scheduled_date, "
            "scheduled_time, video_path, caption, status, post_type, post_format, stat_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (album_slug, album_name, artist, scheduled_date, scheduled_time,
             video_path, caption, post_type, post_format, stat_type)
        )
    print(f"  Queued [{post_type}]: {album_name} for {scheduled_date} {scheduled_time}")


# --- Artist stats tracking ---

def _artist_key(artist_name):
    """Normalize an artist name for de-dupe lookups."""
    return artist_name.strip().lower()


def has_stat_been_used(artist_name, stat_type, db_path=None):
    """Check if (artist, stat_type) is already in the queue (any status) or recorded as posted."""
    key = _artist_key(artist_name)
    with get_db(db_path) as conn:
        # Already recorded as queued/posted in the dedupe table
        row = conn.execute(
            "SELECT id FROM posted_artist_stats WHERE artist_key = ? AND stat_type = ?",
            (key, stat_type),
        ).fetchone()
        if row:
            return True
        # Or sitting in the queue right now (not yet recorded for some reason)
        row = conn.execute(
            "SELECT id FROM queue WHERE artist = ? COLLATE NOCASE AND stat_type = ? "
            "AND status != 'rejected'",
            (artist_name, stat_type),
        ).fetchone()
    return row is not None


def record_stat_used(artist_name, stat_type, db_path=None):
    """Mark an (artist, stat_type) as used so we don't re-queue the same combo."""
    key = _artist_key(artist_name)
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO posted_artist_stats (artist_key, stat_type) VALUES (?, ?)",
            (key, stat_type),
        )


def list_used_stats_for_artist(artist_name, db_path=None):
    """Return the set of stat_types already used for an artist (queued or recorded)."""
    key = _artist_key(artist_name)
    used = set()
    with get_db(db_path) as conn:
        for r in conn.execute(
            "SELECT stat_type FROM posted_artist_stats WHERE artist_key = ?", (key,)
        ).fetchall():
            used.add(r["stat_type"])
        # Include things sitting in the queue right now
        for r in conn.execute(
            "SELECT DISTINCT stat_type FROM queue "
            "WHERE artist = ? COLLATE NOCASE AND stat_type IS NOT NULL "
            "AND status != 'rejected'",
            (artist_name,),
        ).fetchall():
            used.add(r["stat_type"])
    return used


def get_last_scheduled_entry(db_path=None):
    """Return the entry with the latest (date, time) — used to plan the next slot."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM queue WHERE status != 'rejected' "
            "ORDER BY scheduled_date DESC, scheduled_time DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_last_album_review_artist(db_path=None):
    """Return the artist of the most recently scheduled album_review entry, or None."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT artist FROM queue "
            "WHERE post_type = 'album_review' AND status != 'rejected' "
            "ORDER BY scheduled_date DESC, scheduled_time DESC LIMIT 1"
        ).fetchone()
    return row["artist"] if row else None


def recent_filler_artists(limit=5, db_path=None):
    """Return the last N filler artists (newest first), so the picker avoids repeats."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT artist FROM queue WHERE post_type = 'filler' AND status != 'rejected' "
            "ORDER BY scheduled_date DESC, scheduled_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["artist"] for r in rows]


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
