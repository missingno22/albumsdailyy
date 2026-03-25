"""
Generate upcoming week's content and queue it in Google Sheets for review.

Reads the posting schedule, generates reels for each slot, uploads to Drive,
and adds rows to the Google Sheets queue with status "pending".

Usage:
    # Generate next 7 days of content
    python tools/generate_queue.py

    # Generate specific number of days ahead
    python tools/generate_queue.py --days 3

    # Dry run (show what would be generated without doing it)
    python tools/generate_queue.py --dry-run

Schedule (default UTC times — user sets exact post_time in the Sheet):
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
import subprocess
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, PROJECT_ROOT)

from tools.google_sheets import SheetsQueue
from tools.google_drive import DriveStorage

# Weekly schedule: day_of_week (0=Mon) -> {post_time_utc: reel_type}
# Default times: 16:00 UTC (12pm EDT) and 23:00 UTC (7pm EDT)
# User can edit the exact post_time in the Google Sheet after generation.
SCHEDULE = {
    0: {"16:00": "full", "23:00": "engagement"},      # Monday
    1: {"16:00": "engagement", "23:00": "short"},      # Tuesday
    2: {"16:00": "full", "23:00": "engagement"},       # Wednesday
    3: {"16:00": "engagement", "23:00": "short"},      # Thursday
    4: {"16:00": "full", "23:00": "engagement"},       # Friday
    5: {"16:00": "engagement", "23:00": "short"},      # Saturday
    6: {"16:00": "engagement"},                         # Sunday (no evening)
}


def validate_albums():
    """Check that enough album files exist. Exits with error if fewer than 3."""
    albums_dir = os.path.join(PROJECT_ROOT, "albums")
    if not os.path.exists(albums_dir):
        print("ERROR: albums/ directory not found")
        sys.exit(1)

    album_files = [f for f in os.listdir(albums_dir) if f.endswith(".md")]
    if len(album_files) < 3:
        print(f"ERROR: Need at least 3 album files in albums/, found {len(album_files)}")
        print(f"  Files found: {album_files}")
        print(f"  The weekly schedule needs 3 full reels (Mon/Wed/Fri) + 3 short reels (Tue/Thu/Sat)")
        sys.exit(1)

    print(f"  Album check: {len(album_files)} albums available — OK")
    return album_files


def load_album_queue():
    """Load the album rotation queue."""
    queue_path = os.path.join(PROJECT_ROOT, "album_queue.json")
    with open(queue_path, "r") as f:
        return json.load(f)


def save_album_queue(queue_data):
    """Save updated album queue (advanced index)."""
    queue_path = os.path.join(PROJECT_ROOT, "album_queue.json")
    with open(queue_path, "w") as f:
        json.dump(queue_data, f, indent=2)
        f.write("\n")


def get_next_album(queue_data):
    """Get the next album in rotation and advance the index."""
    albums = queue_data["albums"]
    idx = queue_data["current_index"] % len(albums)
    album = albums[idx]
    queue_data["current_index"] = idx + 1
    return album


def get_next_engagement_reel():
    """Get the next engagement reel from the content directory."""
    content_dir = os.path.join(PROJECT_ROOT, "content", "engagement")
    if not os.path.exists(content_dir):
        print(f"  Warning: {content_dir} not found")
        return None

    reels = sorted([
        f for f in os.listdir(content_dir)
        if f.endswith(".mp4") and not f.endswith("_compat.mp4")
    ])
    if not reels:
        print("  Warning: No engagement reels in content/engagement/")
        return None

    # Track which reel to use next via a simple counter file
    tracker_path = os.path.join(content_dir, ".tracker")
    idx = 0
    if os.path.exists(tracker_path):
        with open(tracker_path, "r") as f:
            try:
                idx = int(f.read().strip()) % len(reels)
            except ValueError:
                idx = 0

    reel = os.path.join(content_dir, reels[idx])

    # Advance tracker
    with open(tracker_path, "w") as f:
        f.write(str(idx + 1))

    return reel


def run_step(description, command):
    """Run a pipeline step."""
    print(f"  {description}...")
    result = subprocess.run(command, cwd=PROJECT_ROOT, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {description} failed")
        print(f"  {result.stderr[-500:]}" if result.stderr else "")
        return False
    return True


def generate_reel(album_path, reel_type):
    """Generate a full or short reel from an album file. Returns output file path."""
    print(f"\n  Generating {reel_type} reel for {album_path}...")

    # Ensure directories
    for d in [".tmp/audio", ".tmp/broll", ".tmp/output"]:
        os.makedirs(os.path.join(PROJECT_ROOT, d), exist_ok=True)

    if reel_type == "full":
        output_file = os.path.join(PROJECT_ROOT, ".tmp", "output", "reel_final.mp4")
        timing_script = "tools/full_reel/calculate_full_timing.py"
        compose_script = "tools/full_reel/compose_full_reel.py"
    else:
        output_file = os.path.join(PROJECT_ROOT, ".tmp", "output", "short_reel_final.mp4")
        timing_script = "tools/short_reel/calculate_short_timing.py"
        compose_script = "tools/short_reel/compose_short_reel.py"

    steps = [
        ("Parsing album rankings", f"python tools/parse_markdown.py {album_path}"),
        ("Downloading audio clips", "python tools/download_audio.py"),
        ("Downloading B-Roll video", "python tools/download_broll.py"),
        ("Calculating timing", f"python {timing_script}"),
        ("Composing reel", f"python {compose_script}"),
    ]

    for desc, cmd in steps:
        if not run_step(desc, cmd):
            return None

    if not os.path.exists(output_file):
        print(f"  ERROR: Output not found at {output_file}")
        return None

    # Re-encode for Instagram compatibility (9:16, H.264)
    compat_file = output_file.replace(".mp4", "_compat.mp4")
    reencode_cmd = (
        f'ffmpeg -y -i "{output_file}" '
        f'-vf "scale=1080:1920:force_original_aspect_ratio=decrease,'
        f'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" '
        f'-c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p '
        f'-c:a aac -b:a 128k -movflags +faststart -r 30 "{compat_file}"'
    )
    if run_step("Re-encoding for Instagram", reencode_cmd):
        return compat_file
    return output_file


def reencode_engagement_reel(reel_path):
    """Re-encode an engagement reel to 9:16 H.264 for Instagram."""
    compat_path = reel_path.replace(".mp4", "_compat.mp4")
    reencode_cmd = (
        f'ffmpeg -y -i "{reel_path}" '
        f'-vf "scale=1080:1920:force_original_aspect_ratio=decrease,'
        f'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" '
        f'-c:v libx264 -profile:v high -pix_fmt yuv420p -r 30 '
        f'-c:a aac -b:a 128k -movflags +faststart "{compat_path}"'
    )
    if run_step("Re-encoding engagement reel", reencode_cmd):
        return compat_path
    return reel_path


def generate_draft_caption(reel_type, album_path=None):
    """Generate a placeholder caption for review."""
    if reel_type == "engagement":
        return "🔥 #kanye #ye #music"

    # Parse album data for a better caption
    if album_path:
        album_data_path = os.path.join(PROJECT_ROOT, ".tmp", "album_data.json")
        if os.path.exists(album_data_path):
            with open(album_data_path, "r") as f:
                data = json.load(f)
            artist = data.get("artist", "")
            album = data.get("album", "")
            song_count = len(data.get("songs", []))
            return (
                f"Every song on {album} by {artist}, rated and ranked. "
                f"{song_count} songs, worst to best. Do you agree with my rankings?\n\n"
                f"#{''.join(artist.lower().split())} #{album.lower().replace(' ', '')} #albumranking #musicreview"
            )

    return "Album ranking — do you agree? #music #albumranking"


def days_until_next_sunday():
    """Calculate days from today through next Sunday (inclusive)."""
    today = datetime.now()
    dow = today.weekday()  # 0=Mon, 6=Sun
    if dow == 6:
        # It's Sunday — generate through next Sunday (7 days)
        return 7
    else:
        # Days remaining this week + next full week through Sunday
        return (6 - dow) + 7


def main():
    default_days = days_until_next_sunday()
    parser = argparse.ArgumentParser(description="Generate and queue upcoming content")
    parser.add_argument("--days", type=int, default=default_days,
                        help=f"Number of days ahead to generate (default: {default_days}, through next Sunday)")
    parser.add_argument("--dry-run", action="store_true", help="Show schedule without generating")
    args = parser.parse_args()

    # Validate we have enough albums before doing anything
    validate_albums()

    album_queue = load_album_queue()
    today = datetime.now()

    # Build list of content to generate
    schedule_items = []
    for day_offset in range(args.days):
        date = today + timedelta(days=day_offset)
        date_str = date.strftime("%Y-%m-%d")
        day_name = date.strftime("%A")
        dow = date.weekday()  # 0=Monday

        day_schedule = SCHEDULE.get(dow, {})
        for post_time, reel_type in day_schedule.items():
            schedule_items.append({
                "date": date_str,
                "day_name": day_name,
                "post_time": post_time,
                "type": reel_type,
            })

    # Show schedule
    print(f"\nContent schedule ({args.days} days from {today.strftime('%Y-%m-%d')}):")
    print(f"{'='*60}")
    for item in schedule_items:
        album = ""
        if item["type"] in ("full", "short"):
            idx = album_queue["current_index"] % len(album_queue["albums"])
            album = f" — {album_queue['albums'][idx]}"
        print(f"  {item['day_name']:9s} {item['date']} {item['post_time']} UTC -> {item['type']}{album}")

    if args.dry_run:
        print(f"\n(Dry run — nothing generated)")
        return

    # Initialize Google services (will fail fast if token is invalid)
    print(f"\nConnecting to Google services...")
    try:
        sheets = SheetsQueue()
        drive = DriveStorage()
        print("  Google services connected — OK")
    except Exception as e:
        print(f"ERROR: Could not connect to Google services: {e}")
        print("  -> Check GOOGLE_TOKEN_JSON and GOOGLE_CREDENTIALS_JSON secrets")
        sys.exit(1)

    generated = 0
    skipped = 0

    for item in schedule_items:
        date_str = item["date"]
        post_time = item["post_time"]
        reel_type = item["type"]

        print(f"\n{'='*60}")
        print(f"{item['day_name']} {date_str} {post_time} UTC — {reel_type}")
        print(f"{'='*60}")

        # Skip if already queued
        if sheets.has_entry(date_str, post_time):
            print(f"  Already queued — skipping")
            skipped += 1
            continue

        # Generate or prepare the video
        video_path = None
        album_path = ""

        if reel_type in ("full", "short"):
            album_path = get_next_album(album_queue)
            video_path = generate_reel(album_path, reel_type)
        elif reel_type == "engagement":
            raw_path = get_next_engagement_reel()
            if raw_path:
                video_path = reencode_engagement_reel(raw_path)

        if not video_path or not os.path.exists(video_path):
            print(f"  ERROR: No video generated — skipping")
            continue

        # Upload to Drive
        drive_url, drive_id = drive.upload_video(video_path)

        # Generate draft caption
        caption = generate_draft_caption(reel_type, album_path)

        # Add to queue
        sheets.add_to_queue(date_str, post_time, reel_type, album_path, drive_url, drive_id, caption)
        generated += 1

    # Save updated album queue
    save_album_queue(album_queue)

    print(f"\n{'='*60}")
    print(f"Done! Generated: {generated}, Skipped: {skipped}")
    print(f"Open your Google Sheet to review and approve posts.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
