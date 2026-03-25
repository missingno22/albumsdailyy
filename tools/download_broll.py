"""
Download B-Roll video clips for each song in the album.

Usage: python tools/download_broll.py [album_data_json] [output_dir]

Strategy:
  1. For each song, try to download its official music video (15s clip)
  2. For songs without a music video, use fallback artist clips (concert, interview, performance)
  3. Download the album cover image for the title card

Outputs clips to .tmp/broll/ with manifest.json.
"""

import json
import os
import subprocess
import sys
import glob


FALLBACK_QUERIES = [
    "{artist} live concert",
    "{artist} interview",
    "{artist} live performance",
]


def download_clip(query, output_path, section="*0:15-0:30"):
    """Download a 15-second video clip from YouTube. Returns True on success."""
    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]",
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
        return os.path.exists(output_path)
    except subprocess.TimeoutExpired:
        print(f"    Download timed out")
        return False


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
        # yt-dlp saves thumbnails with various extensions
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

    manifest = []
    failed_indices = []

    # Phase 1: Per-song music video downloads
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

        query = f"{artist} {song['name']} official music video"
        print(f"  [{i + 1}/{len(songs)}] Downloading: {song['name']} (music video)")

        success = download_clip(query, output_path)
        if success:
            manifest.append({
                "index": i,
                "song": song["name"],
                "file": output_path,
                "type": "music_video",
            })
        else:
            print(f"    No music video found, will use fallback")
            manifest.append({
                "index": i,
                "song": song["name"],
                "file": None,
                "type": "fallback",
            })
            failed_indices.append(i)

    # Phase 2: Download fallback clips for songs that failed
    if failed_indices:
        print(f"\nDownloading {len(FALLBACK_QUERIES)} fallback clips for {len(failed_indices)} songs...")
        fallback_clips = []

        for j, query_template in enumerate(FALLBACK_QUERIES):
            fb_filename = f"fallback_{j + 1:02d}.mp4"
            fb_path = os.path.join(output_dir, fb_filename)

            if os.path.exists(fb_path):
                print(f"  Skipping (exists): {fb_filename}")
                fallback_clips.append(fb_path)
                continue

            query = query_template.format(artist=artist)
            print(f"  Downloading fallback: '{query}'")

            success = download_clip(query, fb_path)
            if success:
                fallback_clips.append(fb_path)
            else:
                print(f"    FAILED: Could not download fallback clip")

        # Assign fallback clips to failed songs (round-robin, no back-to-back)
        if fallback_clips:
            prev_fb = -1
            for idx in failed_indices:
                fb_idx = idx % len(fallback_clips)
                if fb_idx == prev_fb and len(fallback_clips) > 1:
                    fb_idx = (fb_idx + 1) % len(fallback_clips)
                manifest[idx]["file"] = fallback_clips[fb_idx]
                manifest[idx]["type"] = "fallback"
                prev_fb = fb_idx

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
    print(f"\nDone: {successful}/{len(songs)} clips downloaded")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
