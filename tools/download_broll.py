"""
Download B-Roll video clips for each song in the album.

Usage: python tools/download_broll.py [album_data_json] [output_dir]

Strategy:
  1. For each song, search YouTube for the official music video
     - Searches multiple results and picks the one whose title matches the song name
     - Validates aspect ratio (rejects square album art videos) and motion
  2. If no music video is found, use artist concert/interview footage instead
  3. Download the album cover image for the title card

Outputs clips to .tmp/broll/ with manifest.json.
"""

import json
import os
import re
import subprocess
import sys
import glob


# Minimum aspect ratio (width/height) to reject square album art videos
MIN_ASPECT_RATIO = 1.2
MIN_WIDTH = 400
MIN_HEIGHT = 240


def normalize(text):
    """Normalize text for fuzzy matching (lowercase, remove punctuation)."""
    return re.sub(r'[^a-z0-9\s]', '', text.lower()).strip()


def title_matches_song(title, song_name, artist):
    """Check if a YouTube video title is relevant to the song.

    Returns True if the title contains the song name AND the artist name.
    This prevents downloading a random video that just happens to be first result.
    """
    t = normalize(title)
    s = normalize(song_name)
    a = normalize(artist)

    # Artist name must appear in title
    if a not in t:
        # Try just first word of artist name (e.g. "Kanye" from "Kanye West")
        first_name = a.split()[0] if a else ""
        if first_name and first_name not in t:
            return False

    # Song name must appear in title (try full name, then key words)
    if s in t:
        return True

    # Try matching key words from the song name (for songs like "Can't Tell Me Nothing")
    song_words = s.split()
    if len(song_words) >= 2:
        # At least 2 significant words must match
        matched = sum(1 for w in song_words if len(w) > 2 and w in t)
        if matched >= min(2, len([w for w in song_words if len(w) > 2])):
            return True

    return False


def is_real_video(title):
    """Check if a video title suggests it's a real video (not lyric/audio/visualizer)."""
    t = title.lower()
    # Reject these types
    bad_keywords = ['lyric video', 'lyrics video', 'audio only', 'visualizer',
                    'album cover', 'full album', 'playlist', 'reaction',
                    'karaoke', 'instrumental', 'remix', 'slowed', 'reverb',
                    'hours loop', 'hour loop', '1 hour', '10 hours']
    for bad in bad_keywords:
        if bad in t:
            return False
    return True


def search_and_pick(query, song_name, artist, count=5):
    """Search YouTube for multiple results and return the URL of the best match.

    Uses yt-dlp to get metadata for multiple search results, then picks the one
    whose title best matches the song name. Returns (url, title) or (None, None).
    """
    cmd = [
        "yt-dlp",
        f"ytsearch{count}:{query}",
        "--print", "%(id)s\t%(title)s\t%(channel)s\t%(duration)s",
        "--no-download",
        "--no-playlist",
        "--socket-timeout", "30",
        "--no-warnings",
        "-q",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return None, None

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not lines:
            return None, None

        print(f"      Found {len(lines)} results, checking titles...")

        for line in lines:
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            vid_id, title, channel = parts[0], parts[1], parts[2]
            duration = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

            # Skip very short (<30s) or very long (>15min) videos
            if duration > 0 and (duration < 30 or duration > 900):
                print(f"      Skip (duration {duration}s): {title[:60]}")
                continue

            # Check if it's a real video (not lyric/audio/visualizer)
            if not is_real_video(title):
                print(f"      Skip (not real video): {title[:60]}")
                continue

            # Check if title matches the song
            if title_matches_song(title, song_name, artist):
                url = f"https://www.youtube.com/watch?v={vid_id}"
                print(f"      Match: {title[:70]}")
                return url, title
            else:
                print(f"      No match: {title[:60]}")

        return None, None

    except subprocess.TimeoutExpired:
        print(f"      Search timed out")
        return None, None


def download_from_url(url, output_path, section="*0:15-0:30"):
    """Download a 15-second clip from a specific YouTube URL. Returns True on success."""
    cmd = [
        "yt-dlp",
        url,
        "-f", "bestvideo[height<=720][height>=360]+bestaudio/best[height<=720][height>=360]",
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--socket-timeout", "30",
        "--no-warnings",
        "-q",
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if os.path.exists(output_path):
            return validate_clip(output_path)
        return False
    except subprocess.TimeoutExpired:
        print(f"      Download timed out")
        return False


def download_first_result(query, output_path, section="*0:15-0:30"):
    """Download first search result without title verification (for fallbacks)."""
    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "-f", "bestvideo[height<=720][height>=360]+bestaudio/best[height<=720][height>=360]",
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--socket-timeout", "30",
        "--no-warnings",
        "-q",
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if os.path.exists(output_path):
            return validate_clip(output_path)
        return False
    except subprocess.TimeoutExpired:
        print(f"      Download timed out")
        return False


def validate_clip(output_path):
    """Validate a downloaded clip has real video content (not album art or static)."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", output_path],
            capture_output=True, text=True, timeout=15
        )
        if probe.returncode != 0:
            print(f"      Could not probe video -- removing")
            os.remove(output_path)
            return False

        streams = json.loads(probe.stdout).get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)

        if not video:
            print(f"      No video stream found -- removing")
            os.remove(output_path)
            return False

        width = int(video.get("width", 0))
        height = int(video.get("height", 0))
        duration = float(video.get("duration", 0) or 0)
        nb_frames = video.get("nb_frames", "0")

        # Check 1: Must have duration and frames
        if duration < 1.0 and nb_frames != "N/A" and int(nb_frames or 0) <= 1:
            print(f"      Static image (no duration) -- removing")
            os.remove(output_path)
            return False

        # Check 2: Reject square videos (album art topic videos)
        if width > 0 and height > 0:
            aspect = width / height
            if aspect < MIN_ASPECT_RATIO and aspect > (1 / MIN_ASPECT_RATIO):
                print(f"      Square video ({width}x{height}) -- album art, removing")
                os.remove(output_path)
                return False

        # Check 3: Minimum resolution
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            print(f"      Too low resolution ({width}x{height}) -- removing")
            os.remove(output_path)
            return False

        print(f"      OK: {width}x{height}, {duration:.1f}s")
        return True

    except Exception as e:
        print(f"      Validation error: {e}")
        return True


def download_album_cover(album, artist, output_dir):
    """Download album cover thumbnail via yt-dlp."""
    cover_path = os.path.join(output_dir, "album_cover")
    query = f"{artist} {album} album cover"

    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "--write-thumbnail",
        "--skip-download",
        "-o", cover_path,
        "--no-playlist",
        "--socket-timeout", "30",
        "--no-warnings",
        "-q",
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        for ext in ["*.webp", "*.jpg", "*.png"]:
            matches = glob.glob(os.path.join(output_dir, f"album_cover{ext.lstrip('*')}"))
            if matches:
                return matches[0]
        return None
    except subprocess.TimeoutExpired:
        print("    Album cover download timed out")
        return None


def main():
    album_path = sys.argv[1] if len(sys.argv) > 1 else ".tmp/album_data.json"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else ".tmp/broll"

    if not os.path.exists(album_path):
        print(f"Error: Album data not found: {album_path}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    with open(album_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    artist = data["artist"]
    album = data["album"]
    songs = data["songs"]

    print(f"Downloading B-Roll for {album} by {artist} ({len(songs)} songs)...")
    print(f"Strategy: official music video -> concert/interview fallback\n")

    manifest = []
    failed_indices = []

    # Phase 1: Try to find official music videos by verifying titles
    for i, song in enumerate(songs):
        filename = f"{i + 1:02d}.mp4"
        output_path = os.path.join(output_dir, filename)

        if os.path.exists(output_path):
            print(f"  [{i + 1}/{len(songs)}] Skipping (exists): {song['name']}")
            manifest.append({
                "index": i,
                "song": song["name"],
                "file": output_path,
                "type": "music_video",
            })
            continue

        print(f"  [{i + 1}/{len(songs)}] {song['name']}")

        # Search for official music video - verify title matches before downloading
        queries = [
            f"{artist} {song['name']} official video",
            f"{artist} {song['name']} music video",
        ]

        success = False
        for qi, query in enumerate(queries):
            label = "official video" if qi == 0 else "music video"
            print(f"    Searching: {label}...")

            url, title = search_and_pick(query, song['name'], artist, count=5)
            if url:
                print(f"    Downloading: {title[:60]}...")
                success = download_from_url(url, output_path)
                if success:
                    manifest.append({
                        "index": i,
                        "song": song["name"],
                        "file": output_path,
                        "type": "music_video",
                    })
                    break
                else:
                    print(f"      Download/validation failed, trying next query...")

        if not success:
            print(f"    No official music video found -- will use concert/interview")
            manifest.append({
                "index": i,
                "song": song["name"],
                "file": None,
                "type": "fallback",
            })
            failed_indices.append(i)

    # Phase 2: Download concert/interview fallback clips for songs without music videos
    if failed_indices:
        print(f"\n--- Downloading fallback clips for {len(failed_indices)} songs ---")
        # Download a pool of concert/interview clips
        fallback_queries = [
            f"{artist} live concert performance HD",
            f"{artist} interview talking",
            f"{artist} live performance stage HD",
            f"{artist} concert footage crowd",
            f"{artist} live show performance",
            f"{artist} behind the scenes",
        ]
        fallback_clips = []

        for j, query in enumerate(fallback_queries):
            fb_filename = f"fallback_{j + 1:02d}.mp4"
            fb_path = os.path.join(output_dir, fb_filename)

            if os.path.exists(fb_path):
                print(f"  Skipping (exists): {fb_filename}")
                fallback_clips.append(fb_path)
                continue

            print(f"  Downloading: '{query}'")
            success = download_first_result(query, fb_path)
            if success:
                fallback_clips.append(fb_path)
            else:
                print(f"    FAILED")

            # Stop once we have enough variety
            if len(fallback_clips) >= max(len(failed_indices) + 1, 4):
                break

        # Assign fallback clips to failed songs (round-robin, avoid back-to-back)
        if fallback_clips:
            prev_fb = -1
            for idx in failed_indices:
                fb_idx = idx % len(fallback_clips)
                if fb_idx == prev_fb and len(fallback_clips) > 1:
                    fb_idx = (fb_idx + 1) % len(fallback_clips)
                manifest[idx]["file"] = fallback_clips[fb_idx]
                manifest[idx]["type"] = "fallback"
                prev_fb = fb_idx
                print(f"  Assigned {os.path.basename(fallback_clips[fb_idx])} -> {manifest[idx]['song']}")

    # Phase 3: Download album cover
    print(f"\nDownloading album cover...")
    cover_path = download_album_cover(album, artist, output_dir)
    if cover_path:
        print(f"  Album cover saved: {cover_path}")
    else:
        print(f"  Warning: Could not download album cover")

    # Save manifest
    manifest_data = {
        "clips": manifest,
        "album_cover": cover_path,
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    successful = sum(1 for m in manifest if m["file"] is not None)
    mv_count = sum(1 for m in manifest if m["type"] == "music_video" and m["file"])
    fb_count = sum(1 for m in manifest if m["type"] == "fallback" and m["file"])
    print(f"\nDone: {successful}/{len(songs)} clips")
    print(f"  Music videos: {mv_count}")
    print(f"  Fallbacks (concert/interview): {fb_count}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
