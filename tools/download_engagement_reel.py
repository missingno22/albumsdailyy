"""
Download top viral Instagram reels for an artist via hashtag browsing.

Usage:
    # From album markdown (extracts artist name automatically)
    python tools/download_engagement_reel.py albums/sample_album.md

    # Specify artist directly
    python tools/download_engagement_reel.py --artist "The Weeknd"

    # Custom output directory and count
    python tools/download_engagement_reel.py --artist "Kanye West" --output-dir .tmp/engagement --count 3

Strategy:
    1. Load Instagram session from Firefox browser cookies
    2. Hit Instagram's web API to browse hashtags related to the artist
    3. Extract top reel posts, sorted by engagement (likes + comments)
    4. Download the top N reels via yt-dlp
    5. If Instagram fails, fall back to YouTube search for viral/meme clips

Requires:
    - requests installed (pip install requests)
    - Firefox with an active Instagram login session
    - yt-dlp installed
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)


def parse_artist_from_markdown(md_path):
    """Extract artist name from album markdown file (line 2: ## Artist Name)."""
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines:
        line = line.strip()
        if line.startswith("## ") and not line[3:].strip().isdigit():
            return line[3:].strip()
    print(f"Error: Could not find artist name in {md_path}")
    sys.exit(1)


def get_instagram_session():
    """Build an authenticated requests session using Firefox cookies."""
    firefox_profiles = os.path.join(
        os.path.expanduser("~"),
        "AppData", "Roaming", "Mozilla", "Firefox", "Profiles",
    )
    if not os.path.exists(firefox_profiles):
        print("  Firefox profiles directory not found")
        return None

    # Find first profile with cookies
    cookie_db = None
    for profile in os.listdir(firefox_profiles):
        candidate = os.path.join(firefox_profiles, profile, "cookies.sqlite")
        if os.path.exists(candidate):
            cookie_db = candidate
            break

    if not cookie_db:
        print("  No Firefox cookie database found")
        return None

    # Copy DB (Firefox locks it while running)
    tmp = tempfile.mktemp(suffix=".sqlite")
    shutil.copy2(cookie_db, tmp)

    try:
        conn = sqlite3.connect(tmp)
        cookies = dict(
            conn.execute(
                "SELECT name, value FROM moz_cookies "
                "WHERE host LIKE '%instagram.com' ORDER BY lastAccessed DESC"
            ).fetchall()
        )
        conn.close()
    finally:
        os.remove(tmp)

    if "sessionid" not in cookies:
        print("  No Instagram session found in Firefox — log in via Firefox first")
        return None

    # Build requests session
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".instagram.com")
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-IG-App-ID": "936619743392459",
    })

    print(f"  Loaded Instagram session (user: {cookies.get('ds_user_id', '?')})")
    return session


def search_hashtag_reels(session, artist):
    """
    Search Instagram hashtags for the artist via the web API.
    Returns a list of reels sorted by engagement (likes + comments).
    """
    clean_name = re.sub(r"[^a-zA-Z0-9]", "", artist.lower())
    hashtags = [
        clean_name,
        f"{clean_name}meme",
        f"{clean_name}memes",
        f"{clean_name}edit",
    ]

    reels = []

    for tag in hashtags:
        print(f"  Browsing #{tag}...")
        try:
            resp = session.get(
                f"https://www.instagram.com/api/v1/tags/web_info/?tag_name={tag}",
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code}")
                continue

            data = resp.json()
            sections = data.get("data", {}).get("top", {}).get("sections", [])

            tag_reels = 0
            for section in sections:
                layout = section.get("layout_content", {})
                for key in layout:
                    item = layout[key]
                    if not isinstance(item, dict):
                        continue

                    clips = item.get("clips", {}).get("items", [])
                    fill = item.get("fill_items", [])

                    for clip_item in clips + fill:
                        media = clip_item.get("media", {})
                        code = media.get("code", "")
                        if not code:
                            continue

                        caption = media.get("caption", {})
                        caption_text = ""
                        if isinstance(caption, dict):
                            caption_text = caption.get("text", "")[:100]

                        likes = media.get("like_count", 0)
                        comments = media.get("comment_count", 0)

                        reels.append({
                            "code": code,
                            "permalink": f"https://www.instagram.com/reel/{code}/",
                            "likes": likes,
                            "comments": comments,
                            "engagement": likes + comments,
                            "caption": caption_text,
                            "hashtag": tag,
                        })
                        tag_reels += 1

            print(f"    Found {tag_reels} reels")

        except Exception as e:
            print(f"    Error: {e}")

        # Respectful delay between requests
        time.sleep(1)

    # Sort by engagement, dedupe by code
    seen = set()
    unique = []
    for r in sorted(reels, key=lambda x: x["engagement"], reverse=True):
        if r["code"] not in seen:
            seen.add(r["code"])
            unique.append(r)

    return unique


def download_reel_ytdlp(url, output_path):
    """Download an Instagram reel via yt-dlp. Returns True on success."""
    cmd = [
        "yt-dlp",
        url,
        "-f", "bestvideo[height<=1080]+bestaudio/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--socket-timeout", "30",
        "--no-warnings",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if os.path.exists(output_path):
            size_kb = os.path.getsize(output_path) / 1024
            print(f"    Saved: {output_path} ({size_kb:.0f}KB)")
            return True
        else:
            stderr = result.stderr[:200] if result.stderr else "no output"
            print(f"    yt-dlp failed: {stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"    Download timed out")
        return False


def youtube_fallback(artist, output_path):
    """Fall back to YouTube search for viral/meme content about the artist."""
    queries = [
        f"{artist} meme compilation",
        f"{artist} funny moments",
        f"{artist} viral clip",
        f"{artist} best moments edit",
    ]

    for query in queries:
        print(f"  Trying YouTube: '{query}'")
        cmd = [
            "yt-dlp",
            f"ytsearch1:{query}",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "--download-sections", "*0:00-0:30",
            "--merge-output-format", "mp4",
            "-o", output_path,
            "--no-playlist",
            "--socket-timeout", "30",
            "--no-warnings",
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if os.path.exists(output_path):
                size_kb = os.path.getsize(output_path) / 1024
                print(f"  Downloaded: {output_path} ({size_kb:.0f}KB)")
                return True
            else:
                print(f"    Failed, trying next query...")
        except subprocess.TimeoutExpired:
            print(f"    Timed out, trying next query...")

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Download top viral Instagram reels about an artist"
    )
    parser.add_argument(
        "markdown_path",
        nargs="?",
        help="Path to album markdown file (extracts artist name)",
    )
    parser.add_argument(
        "--artist",
        help="Artist name (overrides markdown parsing)",
    )
    parser.add_argument(
        "--output-dir",
        default=".tmp/engagement",
        help="Output directory for downloaded reels",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of reels to download (default: 3)",
    )
    parser.add_argument(
        "--skip-instagram",
        action="store_true",
        help="Skip Instagram and go straight to YouTube fallback",
    )

    args = parser.parse_args()

    # Resolve artist name
    if args.artist:
        artist = args.artist
    elif args.markdown_path:
        if not os.path.exists(args.markdown_path):
            print(f"Error: File not found: {args.markdown_path}")
            sys.exit(1)
        artist = parse_artist_from_markdown(args.markdown_path)
    else:
        print("Error: Provide either a markdown file or --artist flag")
        sys.exit(1)

    print(f"Finding top {args.count} viral reels for: {artist}")
    print(f"Output directory: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    downloaded_count = 0

    # Strategy 1: Instagram hashtag search via web API
    if not args.skip_instagram:
        print(f"\n[1] Loading Instagram session from Firefox...")
        session = get_instagram_session()

        if session:
            print(f"\n[2] Searching hashtags for reels...")
            reels = search_hashtag_reels(session, artist)

            if reels:
                print(f"\n  Found {len(reels)} reels. Top {min(len(reels), args.count)}:")
                for i, r in enumerate(reels[: args.count]):
                    print(
                        f"    {i+1}. {r['likes']:,} likes, {r['comments']:,} comments "
                        f"| #{r['hashtag']}"
                    )

                print(f"\n[3] Downloading top {min(len(reels), args.count)} reels...")
                for i, reel in enumerate(reels[: args.count]):
                    output_path = os.path.join(
                        args.output_dir, f"engagement_reel_{i+1}.mp4"
                    )

                    if os.path.exists(output_path):
                        print(f"  [{i+1}] Already exists: {output_path}")
                        downloaded_count += 1
                        continue

                    print(f"  [{i+1}] Downloading: {reel['permalink']}")
                    success = download_reel_ytdlp(reel["permalink"], output_path)

                    if success:
                        downloaded_count += 1
                    else:
                        print(f"    Skipping, will try next")

                    time.sleep(1)
            else:
                print("  No reels found via Instagram hashtags")
        else:
            print("  Could not establish Instagram session")

    # Strategy 2: YouTube fallback for remaining slots
    remaining = args.count - downloaded_count
    if remaining > 0:
        print(f"\n[Fallback] Downloading {remaining} reel(s) from YouTube...")
        for i in range(remaining):
            idx = downloaded_count + i + 1
            output_path = os.path.join(args.output_dir, f"engagement_reel_{idx}.mp4")
            if not os.path.exists(output_path):
                youtube_fallback(artist, output_path)
                if os.path.exists(output_path):
                    downloaded_count += 1

    # Final status
    print(f"\n{'=' * 50}")
    print(f"Downloaded {downloaded_count}/{args.count} reels to {args.output_dir}/")
    for f in sorted(os.listdir(args.output_dir)):
        if f.endswith(".mp4"):
            size_kb = os.path.getsize(os.path.join(args.output_dir, f)) / 1024
            print(f"  {f} ({size_kb:.0f}KB)")


if __name__ == "__main__":
    main()
