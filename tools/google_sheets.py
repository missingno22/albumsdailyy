"""
Google Sheets helper for the posting queue.

Manages the review queue where reels are staged for human approval before posting.

Sheet columns:
    A: date        — Scheduled post date (YYYY-MM-DD)
    B: post_time   — Posting time in 24h UTC (e.g. "16:00")
    C: type        — "full", "short", or "engagement"
    D: album       — Album markdown path (empty for engagement reels)
    E: drive_url   — Google Drive shareable link to preview video
    F: drive_id    — Google Drive file ID (for download/delete)
    G: caption     — Draft caption (auto-generated, user can edit)
    H: status      — "pending" | "approved" | "posted"
    I: media_id    — Instagram media ID (filled after posting)

Usage:
    from tools.google_sheets import SheetsQueue
    queue = SheetsQueue()
    queue.add_to_queue("2026-03-26", "16:00", "full", "albums/1-CollegeDropout.md",
                       "https://drive.google.com/...", "abc123", "My caption")
    rows = queue.get_ready_to_post()
"""

import os
import json
import time
import ssl
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Column headers for the queue sheet
HEADERS = ["date", "post_time", "type", "album", "drive_url", "drive_id", "caption", "status", "media_id"]


def _retry_api_call(func, max_retries=3, delay=5):
    """Retry a Google API call on transient errors (SSL, connection drops)."""
    for attempt in range(max_retries):
        try:
            return func()
        except (ssl.SSLEOFError, ConnectionError, OSError) as e:
            if attempt < max_retries - 1:
                wait = delay * (attempt + 1)
                print(f"  Network error: {e} — retrying in {wait}s ({attempt + 1}/{max_retries})")
                time.sleep(wait)
            else:
                raise


def _load_env():
    """Load environment variables from .env file."""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars


def get_credentials():
    """Get or refresh Google OAuth credentials. Triggers browser auth if needed."""
    token_path = os.path.join(PROJECT_ROOT, "token.json")
    creds_path = os.path.join(PROJECT_ROOT, "credentials.json")

    # Check for CI environment (GitHub Actions) — use env vars instead of files
    if os.environ.get("GOOGLE_TOKEN_JSON"):
        import base64
        token_data = json.loads(base64.b64decode(os.environ["GOOGLE_TOKEN_JSON"]))
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    elif os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    else:
        creds = None

    # Refresh or re-authorize if needed
    if creds and creds.expired and creds.refresh_token:
        print("  Refreshing Google token...")
        creds.refresh(Request())
        # Save refreshed token locally (not in CI)
        if not os.environ.get("GOOGLE_TOKEN_JSON") and os.path.exists(token_path):
            with open(token_path, "w") as f:
                f.write(creds.to_json())
    elif not creds or not creds.valid:
        if not os.path.exists(creds_path):
            print("Error: credentials.json not found. Run this locally first to authorize.")
            raise FileNotFoundError("credentials.json required for initial authorization")
        print("  Google authorization required — opening browser...")
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print("  Authorization saved to token.json")

    return creds


class SheetsQueue:
    """Manages the Google Sheets posting queue."""

    def __init__(self, sheet_id=None):
        env = _load_env()
        self.sheet_id = sheet_id or os.environ.get("GOOGLE_SHEET_ID") or env.get("GOOGLE_SHEET_ID")
        if not self.sheet_id:
            raise ValueError("GOOGLE_SHEET_ID not set in .env or environment")

        creds = get_credentials()
        self.service = build("sheets", "v4", credentials=creds)
        self.sheet = self.service.spreadsheets()
        self._ensure_headers()

    def _ensure_headers(self):
        """Add header row if the sheet is empty."""
        result = self.sheet.values().get(
            spreadsheetId=self.sheet_id, range="A1:I1"
        ).execute()
        values = result.get("values", [])
        if not values or values[0] != HEADERS:
            self.sheet.values().update(
                spreadsheetId=self.sheet_id,
                range="A1:I1",
                valueInputOption="RAW",
                body={"values": [HEADERS]},
            ).execute()
            print("  Initialized sheet headers")

    def read_all(self):
        """Read all queue rows (excluding header). Returns list of dicts."""
        result = _retry_api_call(lambda: self.sheet.values().get(
            spreadsheetId=self.sheet_id, range="A2:I1000"
        ).execute())
        rows = result.get("values", [])
        entries = []
        for i, row in enumerate(rows):
            # Pad short rows with empty strings
            padded = row + [""] * (len(HEADERS) - len(row))
            entry = dict(zip(HEADERS, padded))
            entry["_row_index"] = i + 2  # 1-indexed, skip header
            entries.append(entry)
        return entries

    def add_to_queue(self, date, post_time, reel_type, album, drive_url, drive_id, caption):
        """Add a new item to the queue with status 'pending'."""
        row = [date, post_time, reel_type, album, drive_url, drive_id, caption, "pending", ""]
        _retry_api_call(lambda: self.sheet.values().append(
            spreadsheetId=self.sheet_id,
            range="A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute())
        print(f"  Queued: {date} {post_time} UTC — {reel_type} ({album or 'engagement'})")

    def get_approved(self, date=None):
        """Get approved items, optionally filtered by date."""
        entries = self.read_all()
        results = []
        for entry in entries:
            if entry["status"] != "approved":
                continue
            if date and entry["date"] != date:
                continue
            results.append(entry)
        return results

    def get_ready_to_post(self):
        """Get approved items whose post_time has passed (based on current UTC time)."""
        from datetime import datetime
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        entries = self.read_all()
        results = []
        for entry in entries:
            if entry["status"] != "approved":
                continue
            # Post if date is today and time has passed, or date is in the past
            if entry["date"] < today:
                results.append(entry)
            elif entry["date"] == today and entry["post_time"] <= current_time:
                results.append(entry)
        return results

    def update_status(self, row_index, status, media_id=None):
        """Update the status (and optionally media_id) for a row."""
        # Update status column (H)
        _retry_api_call(lambda: self.sheet.values().update(
            spreadsheetId=self.sheet_id,
            range=f"H{row_index}",
            valueInputOption="RAW",
            body={"values": [[status]]},
        ).execute())
        # Update media_id column (I) if provided
        if media_id:
            _retry_api_call(lambda: self.sheet.values().update(
                spreadsheetId=self.sheet_id,
                range=f"I{row_index}",
                valueInputOption="RAW",
                body={"values": [[str(media_id)]]},
            ).execute())
        print(f"  Row {row_index}: status -> {status}" + (f", media_id -> {media_id}" if media_id else ""))

    def has_entry(self, date, post_time):
        """Check if an entry already exists for a given date and post time."""
        entries = self.read_all()
        for entry in entries:
            if entry["date"] == date and entry["post_time"] == post_time:
                return True
        return False


if __name__ == "__main__":
    # Quick test: read the queue
    queue = SheetsQueue()
    entries = queue.read_all()
    print(f"\nQueue has {len(entries)} entries:")
    for e in entries:
        print(f"  {e['date']} {e['post_time']} UTC — {e['type']} — {e['status']}")
