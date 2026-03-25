"""
Queue videos from Google Drive into the Google Sheets posting schedule.

Scans the Drive folder for numbered videos (e.g. "1-CollegeDropout.mp4"),
picks up where it left off, and fills the Sheet with rows for the upcoming week.

This runs LOCALLY — video rendering and uploading to Drive happens on your machine.
GitHub Actions only handles posting at the scheduled times.

Usage:
    # Queue next week's posts (through next Sunday)
    python tools/generate_queue.py

    # Queue specific number of days
    python tools/generate_queue.py --days 3

    # Dry run (show what would be queued)
    python tools/generate_queue.py --dry-run

Drive folder convention:
    Videos should be named with a number prefix for ordering:
        1-CollegeDropout.mp4
        2-LateRegistration.mp4
        3-Graduation.mp4
    The system tracks which video is next via video_queue.json.

Schedule (default UTC times — edit post_time in the Sheet):
    Mon 16:00: full    | Mon 23:00: engagement
    Tue 16:00: engagement | Tue 23:00: short
    Wed 16:00: full    | Wed 23:00: engagement
    Thu 16:00: engagement | Thu 23:00: short
    Fri 16:00: full    | Fri 23:00: engagement
    Sat 16:00: engagement | Sat 23:00: short
    Sun 16:00: engagement | Sun 23:00: (none)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, PROJECT_ROOT)

from tools.google_sheets import SheetsQueue
from tools.google_drive import DriveStorage

# Weekly schedule: day_of_week (0=Mon) -> {post_time_utc: reel_type}
# Default times: 16:00 UTC (12pm EDT) and 23:00 UTC (7pm EDT)
# User can edit the exact post_time in the Google Sheet after queuing.
SCHEDULE = {
    0: {"16:00": "full", "23:00": "engagement"},      # Monday
    1: {"16:00": "engagement", "23:00": "short"},      # Tuesday
    2: {"16:00": "full", "23:00": "engagement"},       # Wednesday
    3: {"16:00": "engagement", "23:00": "short"},      # Thursday
    4: {"16:00": "full", "23:00": "engagement"},       # Friday
    5: {"16:00": "engagement", "23:00": "short"},      # Saturday
    6: {"16:00": "engagement"},                         # Sunday (no evening)
}

QUEUE_FILE = os.path.join(PROJECT_ROOT, "video_queue.json")


def load_video_queue():
    """Load the video queue tracker (which Drive video is next)."""
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    return {"current_index": 0}


def save_video_queue(queue_data):
    """Save updated video queue tracker."""
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue_data, f, indent=2)
        f.write("\n")


def parse_video_number(name):
    """Extract the leading number from a filename like '1-CollegeDropout.mp4'."""
    match = re.match(r"^(\d+)", name)
    return int(match.group(1)) if match else None


def get_sorted_drive_videos(drive):
    """Get all videos from Drive folder, sorted by their number prefix."""
    print("  Scanning Drive folder for videos...")
    files = drive.list_videos()

    # Filter to mp4s with number prefixes and sort
    numbered = []
    for f in files:
        num = parse_video_number(f["name"])
        if num is not None and f["name"].lower().endswith(".mp4"):
            numbered.append((num, f))

    numbered.sort(key=lambda x: x[0])

    if not numbered:
        print("  ERROR: No numbered videos found in Drive folder")
        print("  Upload videos named like: 1-CollegeDropout.mp4, 2-LateRegistration.mp4")
        sys.exit(1)

    print(f"  Found {len(numbered)} videos in Drive:")
    for num, f in numbered:
        size_mb = int(f.get("size", 0)) / (1024 * 1024)
        print(f"    {num}. {f['name']} ({size_mb:.1f}MB)")

    return numbered


def get_next_video(numbered_videos, queue_data):
    """Get the next video in rotation and advance the index."""
    idx = queue_data["current_index"] % len(numbered_videos)
    num, video = numbered_videos[idx]
    queue_data["current_index"] = idx + 1
    return video


def days_until_next_sunday():
    """Calculate days from today through next Sunday (inclusive)."""
    today = datetime.now()
    dow = today.weekday()  # 0=Mon, 6=Sun
    if dow == 6:
        return 7
    else:
        return (6 - dow) + 7


def generate_draft_caption(reel_type, video_name=""):
    """Generate a placeholder caption for review."""
    if reel_type == "engagement":
        return "#kanye #ye #music"

    # Try to extract album name from video filename
    # e.g. "1-CollegeDropout.mp4" -> "College Dropout"
    clean = re.sub(r"^\d+[-_]?", "", video_name)
    clean = clean.replace(".mp4", "").replace("_", " ").replace("-", " ").strip()

    if clean:
        return (
            f"Every song on {clean}, rated and ranked. "
            f"Worst to best. Do you agree with my rankings?\n\n"
            f"#albumranking #musicreview #music"
        )

    return "Album ranking -- do you agree? #music #albumranking"


def main():
    default_days = days_until_next_sunday()
    parser = argparse.ArgumentParser(description="Queue Drive videos into the posting schedule")
    parser.add_argument("--days", type=int, default=default_days,
                        help=f"Number of days ahead to queue (default: {default_days}, through next Sunday)")
    parser.add_argument("--dry-run", action="store_true", help="Show schedule without queuing")
    args = parser.parse_args()

    # Connect to Google services
    print("Connecting to Google services...")
    try:
        sheets = SheetsQueue()
        drive = DriveStorage()
        print("  Connected -- OK")
    except Exception as e:
        print(f"ERROR: Could not connect to Google services: {e}")
        sys.exit(1)

    # Scan Drive for numbered videos
    numbered_videos = get_sorted_drive_videos(drive)
    video_queue = load_video_queue()

    # Get engagement reel videos (any video with "engagement" in the name)
    engagement_videos = [(n, f) for n, f in numbered_videos if "engagement" in f["name"].lower()]
    album_videos = [(n, f) for n, f in numbered_videos if "engagement" not in f["name"].lower()]

    if not album_videos:
        print("  ERROR: No album videos found (non-engagement videos)")
        print("  Upload videos named like: 1-CollegeDropout.mp4, 2-LateRegistration.mp4")
        sys.exit(1)

    print(f"\n  Album videos: {len(album_videos)}")
    print(f"  Engagement videos: {len(engagement_videos)}")
    print(f"  Next album index: {video_queue['current_index']}")

    # Build schedule
    today = datetime.now()
    schedule_items = []
    for day_offset in range(args.days):
        date = today + timedelta(days=day_offset)
        date_str = date.strftime("%Y-%m-%d")
        day_name = date.strftime("%A")
        dow = date.weekday()

        day_schedule = SCHEDULE.get(dow, {})
        for post_time, reel_type in day_schedule.items():
            schedule_items.append({
                "date": date_str,
                "day_name": day_name,
                "post_time": post_time,
                "type": reel_type,
            })

    # Show schedule
    print(f"\nPosting schedule ({args.days} days from {today.strftime('%Y-%m-%d')}):")
    print(f"{'='*60}")
    album_idx_preview = video_queue["current_index"]
    for item in schedule_items:
        video_label = ""
        if item["type"] in ("full", "short"):
            idx = album_idx_preview % len(album_videos)
            video_label = f" -- {album_videos[idx][1]['name']}"
            album_idx_preview += 1
        elif item["type"] == "engagement" and engagement_videos:
            video_label = " -- (engagement)"
        print(f"  {item['day_name']:9s} {item['date']} {item['post_time']} UTC -> {item['type']}{video_label}")

    if args.dry_run:
        print(f"\n(Dry run -- nothing queued)")
        return

    # Queue items
    engagement_idx = 0
    queued = 0
    skipped = 0

    for item in schedule_items:
        date_str = item["date"]
        post_time = item["post_time"]
        reel_type = item["type"]

        print(f"\n{'='*60}")
        print(f"{item['day_name']} {date_str} {post_time} UTC -- {reel_type}")
        print(f"{'='*60}")

        # Skip if already queued
        if sheets.has_entry(date_str, post_time):
            print(f"  Already queued -- skipping")
            skipped += 1
            continue

        # Pick the video
        if reel_type in ("full", "short"):
            video = get_next_video(album_videos, video_queue)
        elif reel_type == "engagement":
            if engagement_videos:
                _, video = engagement_videos[engagement_idx % len(engagement_videos)]
                engagement_idx += 1
            else:
                print("  WARNING: No engagement videos in Drive -- skipping")
                continue
        else:
            continue

        drive_url = video.get("webViewLink", f"https://drive.google.com/file/d/{video['id']}/view")
        drive_id = video["id"]
        caption = generate_draft_caption(reel_type, video["name"])

        sheets.add_to_queue(date_str, post_time, reel_type, video["name"], drive_url, drive_id, caption)
        queued += 1

    # Save updated queue tracker
    save_video_queue(video_queue)

    print(f"\n{'='*60}")
    print(f"Done! Queued: {queued}, Skipped (already queued): {skipped}")
    print(f"Next album index: {video_queue['current_index']}")
    print(f"\nOpen your Google Sheet to review, edit captions, and approve:")
    print(f"  Set post_time (24h UTC) and status -> 'approved'")
    print(f"  GitHub Actions posts every 30 min when approved items are due.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
