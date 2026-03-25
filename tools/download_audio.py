"""
Download audio for each song from YouTube using yt-dlp.

Usage: python tools/download_audio.py [album_data_json] [output_dir]

Reads .tmp/album_data.json, downloads MP3s to .tmp/audio/,
and writes .tmp/audio/manifest.json.
"""

import json
import os
import re
import subprocess
import sys


def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def download_song(artist, song_name, output_path, attempt=1):
    """Download a song's audio from YouTube. Returns True on success."""
    if attempt == 1:
        query = f"{artist} {song_name} audio"
    else:
        query = f"{artist} {song_name}"

    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "-x",
        "--audio-format", "mp3",
        "-o", output_path,
        "--no-playlist",
        "--socket-timeout", "30",
        "--no-warnings",
        "-q",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        # yt-dlp may add .mp3 extension if not already present
        expected = output_path if output_path.endswith(".mp3") else output_path + ".mp3"
        if os.path.exists(output_path) or os.path.exists(expected):
            return True
        if result.returncode != 0 and attempt == 1:
            print(f"    Retrying with broader query...")
            return download_song(artist, song_name, output_path, attempt=2)
        return False
    except subprocess.TimeoutExpired:
        print(f"    Download timed out")
        return False


def main():
    album_path = sys.argv[1] if len(sys.argv) > 1 else ".tmp/album_data.json"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else ".tmp/audio"

    if not os.path.exists(album_path):
        print(f"Error: Album data not found: {album_path}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    with open(album_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    artist = data["artist"]
    songs = data["songs"]
    manifest = []

    print(f"Downloading audio for {len(songs)} songs by {artist}...")

    for i, song in enumerate(songs):
        safe_name = sanitize_filename(song["name"])
        filename = f"{i + 1:02d}_{safe_name}.mp3"
        output_path = os.path.join(output_dir, filename)

        # Skip if already downloaded
        if os.path.exists(output_path):
            print(f"  [{i + 1}/{len(songs)}] Skipping (exists): {song['name']}")
            manifest.append({
                "index": i,
                "song": song["name"],
                "rating": song["rating"],
                "file": output_path,
            })
            continue

        print(f"  [{i + 1}/{len(songs)}] Downloading: {song['name']}")
        success = download_song(artist, song["name"], output_path)

        if success:
            manifest.append({
                "index": i,
                "song": song["name"],
                "rating": song["rating"],
                "file": output_path,
            })
        else:
            print(f"    FAILED: Could not download '{song['name']}'")
            manifest.append({
                "index": i,
                "song": song["name"],
                "rating": song["rating"],
                "file": None,
            })

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    successful = sum(1 for m in manifest if m["file"] is not None)
    print(f"\nDone: {successful}/{len(songs)} songs downloaded")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
