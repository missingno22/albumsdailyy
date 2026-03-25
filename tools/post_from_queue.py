"""
Post approved items from the Google Sheets queue to Instagram.

Checks the queue for approved items whose scheduled post_time (UTC) has passed,
then posts them.

Usage:
    # Post all approved items that are due now
    python tools/post_from_queue.py

    # Dry run (show what would be posted)
    python tools/post_from_queue.py --dry-run
"""

import argparse
import os
import subprocess
import sys
import json
from datetime import datetime

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, PROJECT_ROOT)

from tools.google_sheets import SheetsQueue
from tools.google_drive import DriveStorage


def load_env():
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


def check_instagram_token(user_id, access_token):
    """Verify the Instagram access token is still valid. Returns True if OK."""
    import urllib.request
    import urllib.error
    url = f"https://graph.facebook.com/v25.0/{user_id}?fields=id&access_token={access_token}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("id"):
                print("  Instagram token: valid — OK")
                return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ERROR: Instagram token invalid or expired (HTTP {e.code})")
        print(f"  {body[:200]}")
        print(f"  -> Update the INSTAGRAM_ACCESS_TOKEN secret in GitHub")
        return False
    except Exception as e:
        print(f"  WARNING: Could not verify Instagram token: {e}")
        # Don't block on network issues — let the actual post attempt decide
        return True


def reencode_for_instagram(input_path):
    """Re-encode video to 9:16 H.264 if needed. Returns path to post."""
    # Probe the video to check format
    probe_cmd = f'ffprobe -v quiet -print_format json -show_streams "{input_path}"'
    result = subprocess.run(probe_cmd, shell=True, capture_output=True, text=True)

    needs_reencode = True
    if result.returncode == 0:
        try:
            streams = json.loads(result.stdout).get("streams", [])
            video = next((s for s in streams if s["codec_type"] == "video"), None)
            if video:
                codec = video.get("codec_name", "")
                w, h = int(video.get("width", 0)), int(video.get("height", 0))
                if codec == "h264" and w == 1080 and h == 1920:
                    needs_reencode = False
                    print(f"  Video already 1080x1920 H.264 — no re-encode needed")
        except (json.JSONDecodeError, KeyError, StopIteration):
            pass

    if not needs_reencode:
        return input_path

    output_path = input_path.replace(".mp4", "_ig.mp4")
    print(f"  Re-encoding to 1080x1920 H.264...")
    cmd = (
        f'ffmpeg -y -i "{input_path}" '
        f'-vf "scale=1080:1920:force_original_aspect_ratio=decrease,'
        f'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" '
        f'-c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p '
        f'-c:a aac -b:a 128k -movflags +faststart -r 30 "{output_path}"'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0 and os.path.exists(output_path):
        return output_path

    print(f"  Warning: Re-encode failed, using original")
    return input_path


def post_reel(video_path, caption, user_id, access_token):
    """Post a reel to Instagram. Returns media_id or None."""
    # Import and use the existing posting function
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
    from post_to_instagram import post_reel as ig_post_reel

    try:
        media_id = ig_post_reel(user_id, access_token, video_path, caption)
        return str(media_id)
    except SystemExit:
        # post_to_instagram uses sys.exit on errors
        return None


def main():
    parser = argparse.ArgumentParser(description="Post approved items from the queue")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be posted without doing it")
    args = parser.parse_args()

    # Load credentials
    env = load_env()
    user_id = os.environ.get("INSTAGRAM_USER_ID") or env.get("INSTAGRAM_USER_ID")
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN") or env.get("INSTAGRAM_ACCESS_TOKEN")

    if not user_id or not access_token:
        print("Error: INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN required")
        sys.exit(1)

    # Verify Instagram token before doing any work
    print("Checking credentials...")
    if not check_instagram_token(user_id, access_token):
        sys.exit(1)

    # Connect to Google services
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"Checking queue for items due by {now_utc}...")
    sheets = SheetsQueue()
    drive = DriveStorage()

    # Get approved items whose post_time has passed
    ready = sheets.get_ready_to_post()

    if not ready:
        print("No approved items due for posting right now.")
        print("  (Items need status 'approved' and a post_time that has passed)")
        return

    print(f"Found {len(ready)} item(s) ready to post")

    for item in ready:
        print(f"\n{'='*50}")
        print(f"Posting: {item['type']} reel — scheduled {item['date']} {item['post_time']} UTC")
        print(f"  Caption: {item['caption'][:80]}{'...' if len(item['caption']) > 80 else ''}")
        print(f"  Drive: {item['drive_url']}")
        print(f"{'='*50}")

        if args.dry_run:
            print("  (Dry run — skipping)")
            continue

        # Download from Drive
        download_dir = os.path.join(PROJECT_ROOT, ".tmp", "queue_download")
        os.makedirs(download_dir, exist_ok=True)
        download_path = os.path.join(download_dir, f"reel_{item['date']}_{item['post_time'].replace(':', '')}.mp4")

        try:
            drive.download_video(item["drive_id"], download_path)
        except Exception as e:
            print(f"  ERROR downloading from Drive: {e}")
            continue

        # Re-encode if needed
        post_path = reencode_for_instagram(download_path)

        # Post to Instagram
        print(f"\n  Posting to Instagram...")
        media_id = post_reel(post_path, item["caption"], user_id, access_token)

        if media_id:
            sheets.update_status(item["_row_index"], "posted", media_id=media_id)
            print(f"  Posted! Media ID: {media_id}")

            # Cleanup local downloaded files
            for f in [download_path, post_path]:
                if os.path.exists(f):
                    os.remove(f)
        else:
            print(f"  ERROR: Instagram posting failed")
            sheets.update_status(item["_row_index"], "error")

    print(f"\nDone!")


if __name__ == "__main__":
    main()
