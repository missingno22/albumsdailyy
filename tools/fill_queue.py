"""
Fill the posting queue with a 7-day buffer of endcard clips.

Usage:
    python tools/fill_queue.py
    python tools/fill_queue.py --days 14
    python tools/fill_queue.py --draft

Scans albums/ for album markdown files, generates endcard videos for unposted
albums, and queues them in SQLite. Stops when all albums have been queued.
"""

import argparse
import glob
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from parse_markdown import parse_album_markdown
from flask_app.models import (
    init_db, has_entry_for_date, is_album_queued_or_posted,
    insert_queue_entry, get_rotation_index, advance_rotation,
    get_pending_count, get_buffer_days,
)


def get_albums():
    """Get all album markdown files sorted by number prefix."""
    pattern = os.path.join(PROJECT_ROOT, "albums", "*.md")
    files = glob.glob(pattern)

    def sort_key(path):
        name = os.path.basename(path)
        match = re.match(r"(\d+)", name)
        return int(match.group(1)) if match else 999

    return sorted(files, key=sort_key)


def get_slug(album_path):
    """'albums/1-CollegeDropout.md' -> '1-CollegeDropout'"""
    return os.path.splitext(os.path.basename(album_path))[0]


def generate_caption(album_data):
    """Auto-generate a caption for the endcard post."""
    songs = album_data["songs"]
    avg = sum(s["rating"] for s in songs) / len(songs)
    top_song = min(songs, key=lambda s: s["rank"])
    return (
        f"Every song on {album_data['album']} by {album_data['artist']}, "
        f"rated and ranked.\n\n"
        f"Average: {avg:.1f}/10\n"
        f"#1: {top_song['name']} - {top_song['rating']:.0f}/10\n\n"
        f"Do you agree with my rankings?\n\n"
        f"#albumranking #musicreview "
        f"#{album_data['artist'].lower().replace(' ', '')} "
        f"#{album_data['album'].lower().replace(' ', '').replace(':', '')}"
    )


def ensure_endcard(album_path, slug, draft=False):
    """Generate endcard video if it doesn't exist. Returns video path."""
    video_path = os.path.join(PROJECT_ROOT, "data", "endcards", f"{slug}.mp4")

    if os.path.exists(video_path):
        print(f"  [endcard] Already exists: {video_path}")
        return video_path

    print(f"  [endcard] Generating for {slug}...")
    cmd = [
        sys.executable, "-u",
        os.path.join(PROJECT_ROOT, "tools", "generate_endcard_lite.py"),
        album_path,
        "--output", video_path,
    ]
    if draft:
        cmd.append("--draft")

    # Stream generate_endcard.py output line-by-line so it shows in the terminal
    process = subprocess.Popen(
        cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in process.stdout:
        print(f"    {line.rstrip()}", flush=True)
    process.wait(timeout=900)

    if process.returncode != 0:
        print(f"  [endcard] ERROR: generation failed for {slug} (exit code {process.returncode})")
        return None

    if os.path.exists(video_path):
        print(f"  [endcard] Done: {video_path}")
        return video_path

    print(f"  [endcard] ERROR: file not created: {video_path}")
    return None


def fill_queue(days_ahead=7, draft=False):
    """Fill the queue with endcard posts for the next N days."""
    init_db()
    albums = get_albums()

    if not albums:
        print("No album files found in albums/")
        return

    print(f"[scan] Found {len(albums)} albums: {[get_slug(a) for a in albums]}")

    # Check which albums are still unposted
    unposted = []
    already_done = []
    for album_path in albums:
        slug = get_slug(album_path)
        if is_album_queued_or_posted(slug):
            already_done.append(slug)
        else:
            unposted.append(album_path)

    if already_done:
        print(f"[scan] Already queued/posted ({len(already_done)}): {already_done}")

    if not unposted:
        print("[scan] All albums have been queued or posted. Nothing to fill.")
        return

    print(f"[scan] Unposted ({len(unposted)}): {[get_slug(a) for a in unposted]}")

    rotation_idx = get_rotation_index()
    added = 0
    skipped_dates = []
    errors = []
    today = datetime.now().date()

    print(f"\n[fill] Filling {days_ahead} days starting from {today + timedelta(days=1)}")

    for day_offset in range(days_ahead):
        target_date = today + timedelta(days=day_offset + 1)  # start from tomorrow
        date_str = target_date.strftime("%Y-%m-%d")

        # Skip if this date already has an entry
        if has_entry_for_date(date_str):
            skipped_dates.append(date_str)
            continue

        # Pick next unposted album
        if not unposted:
            print(f"[fill] {date_str}: no more unposted albums, stopping")
            break

        album_path = unposted.pop(0)
        slug = get_slug(album_path)

        print(f"\n[fill] {date_str} -> {slug}")

        # Parse album data
        print(f"  [parse] Reading {os.path.basename(album_path)}...")
        album_data = parse_album_markdown(album_path)
        print(f"  [parse] {album_data['album']} by {album_data['artist']} ({album_data['total_songs']} songs)")

        # Generate endcard
        video_path = ensure_endcard(album_path, slug, draft=draft)
        if not video_path:
            msg = f"{slug}: endcard generation failed"
            print(f"  [error] {msg}")
            errors.append(msg)
            continue

        # Generate caption
        caption = generate_caption(album_data)
        print(f"  [caption] Generated ({len(caption)} chars)")

        # Insert into queue
        insert_queue_entry(
            album_slug=slug,
            album_name=album_data["album"],
            artist=album_data["artist"],
            scheduled_date=date_str,
            video_path=video_path,
            caption=caption,
        )

        advance_rotation()
        added += 1
        print(f"  [queued] {album_data['album']} scheduled for {date_str}")

    # Summary
    counts = get_pending_count()
    buffer = get_buffer_days()
    print(f"\n{'='*40}")
    print(f"Added {added} entries to queue")
    if skipped_dates:
        print(f"Skipped {len(skipped_dates)} dates (already filled): {skipped_dates}")
    if errors:
        print(f"Errors ({len(errors)}): {errors}")
    print(f"Queue: {counts}")
    print(f"Buffer: {buffer} days")


def main():
    parser = argparse.ArgumentParser(description="Fill posting queue with endcard clips")
    parser.add_argument("--days", type=int, default=7, help="Days ahead to fill (default: 7)")
    parser.add_argument("--draft", action="store_true", help="Generate draft-quality endcards")
    args = parser.parse_args()

    fill_queue(days_ahead=args.days, draft=args.draft)


if __name__ == "__main__":
    main()
