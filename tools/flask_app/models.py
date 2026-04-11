"""
SQLite database models for the Reel Scheduler posting queue.

Schema:
  instagram_accounts - one row per Instagram account
  queue              - one row per scheduled video upload
  automations        - one row per account's linked fill-queue automation
"""

import os
import sqlite3
from contextlib import contextmanager

# Database lives at data/queue.db (project root)
_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "queue.db")


@contextmanager
def get_db(db_path=None):
    """Context manager for database connections with WAL journaling."""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
            CREATE TABLE IF NOT EXISTS instagram_accounts (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                name                 TEXT NOT NULL UNIQUE,
                display_name         TEXT,
                instagram_user_id    TEXT NOT NULL,
                access_token         TEXT NOT NULL,
                created_at           TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS automations (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id          INTEGER NOT NULL UNIQUE REFERENCES instagram_accounts(id) ON DELETE CASCADE,
                name                TEXT NOT NULL,
                script_command      TEXT NOT NULL,
                working_directory   TEXT NOT NULL,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id          INTEGER NOT NULL REFERENCES instagram_accounts(id) ON DELETE CASCADE,
                title               TEXT NOT NULL,
                video_path          TEXT NOT NULL,
                video_filename      TEXT NOT NULL,
                caption             TEXT NOT NULL DEFAULT '',
                scheduled_datetime  TEXT NOT NULL,
                drive_file_id       TEXT,
                drive_public_url    TEXT,
                status              TEXT NOT NULL DEFAULT 'pending',
                instagram_media_id  TEXT,
                created_at          TEXT DEFAULT (datetime('now')),
                posted_at           TEXT,
                error_message       TEXT
            );
        """)
    print("Database initialized.", flush=True)


# --- Account CRUD ---

def list_accounts(db_path=None):
    """Return all Instagram accounts with pending/approved/posted counts."""
    with get_db(db_path) as conn:
        rows = conn.execute("""
            SELECT a.*,
                   COUNT(q.id) as total_entries,
                   SUM(CASE WHEN q.status = 'pending' THEN 1 ELSE 0 END) as pending_count
            FROM instagram_accounts a
            LEFT JOIN queue q ON q.account_id = a.id
            GROUP BY a.id
            ORDER BY a.name ASC
        """).fetchall()
    return [dict(r) for r in rows]


def get_account(account_id, db_path=None):
    """Return a single account by ID."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM instagram_accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


def insert_account(name, display_name, instagram_user_id, access_token, db_path=None):
    """Insert a new Instagram account."""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO instagram_accounts (name, display_name, instagram_user_id, access_token) "
            "VALUES (?, ?, ?, ?)",
            (name.lstrip("@"), display_name or name, instagram_user_id, access_token)
        )
    print(f"  Account added: @{name}", flush=True)


def update_account(account_id, name, display_name, instagram_user_id, access_token, db_path=None):
    """Update an existing account."""
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE instagram_accounts SET name=?, display_name=?, instagram_user_id=?, access_token=? WHERE id=?",
            (name.lstrip("@"), display_name or name, instagram_user_id, access_token, account_id)
        )


def delete_account(account_id, db_path=None):
    """Delete an account (cascades to queue entries)."""
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM instagram_accounts WHERE id = ?", (account_id,))


# --- Queue CRUD ---

def get_all_queue(account_id, db_path=None):
    """Return all queue entries for an account, ordered by scheduled datetime."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM queue WHERE account_id = ? ORDER BY scheduled_datetime ASC",
            (account_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_queue_entry(entry_id, db_path=None):
    """Return a single queue entry by ID, joined with its account."""
    with get_db(db_path) as conn:
        row = conn.execute("""
            SELECT q.*, a.name as account_name, a.display_name as account_display_name,
                   a.instagram_user_id, a.access_token
            FROM queue q
            JOIN instagram_accounts a ON a.id = q.account_id
            WHERE q.id = ?
        """, (entry_id,)).fetchone()
    return dict(row) if row else None


def get_pending_count(account_id, db_path=None):
    """Count queue entries by status for an account."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM queue WHERE account_id = ? GROUP BY status",
            (account_id,)
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


def insert_queue_entry(account_id, title, video_path, video_filename, caption, scheduled_datetime, db_path=None):
    """Insert a new queue entry with status 'pending'."""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO queue (account_id, title, video_path, video_filename, caption, scheduled_datetime, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (account_id, title, video_path, video_filename, caption, scheduled_datetime)
        )
    print(f"  Queued: {title} for {scheduled_datetime}", flush=True)


def update_status(entry_id, status, db_path=None, **kwargs):
    """Update the status of a queue entry. Extra kwargs set additional columns."""
    allowed = {"drive_file_id", "drive_public_url", "instagram_media_id", "error_message", "caption"}
    sets = ["status = ?"]
    params = [status]

    if status == "posted":
        sets.append("posted_at = datetime('now')")

    for key, value in kwargs.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            params.append(value)

    params.append(entry_id)
    with get_db(db_path) as conn:
        conn.execute(
            f"UPDATE queue SET {', '.join(sets)} WHERE id = ?",
            params
        )


def update_entry(entry_id, caption, scheduled_datetime, db_path=None):
    """Update caption and scheduled datetime for a queue entry."""
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE queue SET caption = ?, scheduled_datetime = ? WHERE id = ?",
            (caption, scheduled_datetime, entry_id)
        )


def delete_queue_entry(entry_id, db_path=None):
    """Delete a queue entry. Returns the video_path and drive_file_id for cleanup."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT video_path, drive_file_id FROM queue WHERE id = ?", (entry_id,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM queue WHERE id = ?", (entry_id,))
    return dict(row) if row else None


# --- Automation CRUD ---

def get_automation(account_id, db_path=None):
    """Return the automation config for an account, or None."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM automations WHERE account_id = ?", (account_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_automation(account_id, name, script_command, working_directory, db_path=None):
    """Insert or update the automation for an account."""
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO automations (account_id, name, script_command, working_directory) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET name=?, script_command=?, working_directory=?",
            (account_id, name, script_command, working_directory,
             name, script_command, working_directory)
        )
    print(f"  Automation saved: {name}", flush=True)


def delete_automation(account_id, db_path=None):
    """Remove the automation config for an account."""
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM automations WHERE account_id = ?", (account_id,))
