"""
Generate a standalone 15-second endcard clip for an album.

Usage:
    python tools/generate_endcard.py albums/1-CollegeDropout.md
    python tools/generate_endcard.py albums/1-CollegeDropout.md --output data/endcards/1-CollegeDropout.mp4
    python tools/generate_endcard.py albums/1-CollegeDropout.md --draft

Pipeline:
    1. Parse album markdown -> album_data
    2. Download B-Roll (cached in .tmp/endcard_assets/<slug>/broll/)
    3. Download #1 song audio (cached in .tmp/endcard_assets/<slug>/audio/)
    4. Render endcard with build_end_card(duration=15.0)
    5. Attach peak 15s of #1 song audio with fadeout
    6. Export as Instagram-compatible H.264 MP4
"""

import argparse
import json
import os
import subprocess
import sys
import time

# Add project root to path for imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))  # parent of albumsdailyy for package imports

from albumsdailyy.tools.parse_markdown import parse_album_markdown
from albumsdailyy.tools.shared.video_utils import build_end_card, find_peak_segment, FPS


ENDCARD_DURATION = 15.0
AUDIO_FADEOUT = 3.0


def get_slug(album_path):
    """Extract slug from album path: 'albums/1-CollegeDropout.md' -> '1-CollegeDropout'."""
    return os.path.splitext(os.path.basename(album_path))[0]


def ensure_broll(album_data, asset_dir):
    """Download B-Roll for the album if not already cached. Returns manifest dict."""
    broll_dir = os.path.join(asset_dir, "broll")
    manifest_path = os.path.join(broll_dir, "manifest.json")

    if os.path.exists(manifest_path):
        print(f"  B-Roll cached: {broll_dir}")
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Write temp album_data for download_broll.py
    os.makedirs(broll_dir, exist_ok=True)
    album_data_path = os.path.join(asset_dir, "album_data.json")
    with open(album_data_path, "w", encoding="utf-8") as f:
        json.dump(album_data, f, indent=2, ensure_ascii=False)

    print(f"  Downloading B-Roll...", flush=True)
    process = subprocess.Popen(
        [sys.executable, "-u", os.path.join(PROJECT_ROOT, "tools", "download_broll.py"),
         album_data_path, broll_dir],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if line:
            print(f"    [broll] {line.rstrip()}", flush=True)
    try:
        process.wait(timeout=600)
    except subprocess.TimeoutExpired:
        process.kill()
        print(f"  Warning: B-Roll download timed out", flush=True)
    if process.returncode and process.returncode != 0:
        print(f"  Warning: B-Roll download had errors (exit code {process.returncode})", flush=True)

    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    return {"clips": [], "album_cover": None}


def ensure_audio(album_data, asset_dir):
    """Download the #1 ranked song audio. Returns path to audio file or None."""
    audio_dir = os.path.join(asset_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    # Find the #1 ranked song (rank 1 = best)
    songs = album_data["songs"]
    top_song = min(songs, key=lambda s: s["rank"])
    artist = album_data["artist"]

    safe_name = top_song["name"].replace("/", "").replace("\\", "").replace(":", "").strip()
    audio_path = os.path.join(audio_dir, f"top_song_{safe_name}.mp3")

    if os.path.exists(audio_path):
        print(f"  Audio cached: {top_song['name']}")
        return audio_path

    print(f"  Downloading audio: {top_song['name']}...")

    # Try downloading with yt-dlp
    queries = [
        f"{artist} {top_song['name']} audio",
        f"{artist} {top_song['name']}",
    ]

    for query in queries:
        print(f"  Trying: {query}", flush=True)
        cmd = [
            "yt-dlp",
            f"ytsearch1:{query}",
            "-x", "--audio-format", "mp3",
            "-o", audio_path,
            "--no-playlist",
            "--socket-timeout", "30",
            "--no-warnings", "--newline",
        ]
        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    print(f"    [audio] {line.rstrip()}", flush=True)
            process.wait(timeout=90)
            if os.path.exists(audio_path):
                print(f"  Audio downloaded: {audio_path}", flush=True)
                return audio_path
        except subprocess.TimeoutExpired:
            process.kill()
            print(f"  Audio download timed out for: {query}", flush=True)
            continue

    print(f"  Warning: Could not download audio for '{top_song['name']}'", flush=True)
    return None


def render_endcard(album_data, broll_manifest, broll_dir, audio_path, output_path, draft=False):
    """Render the 15-second endcard clip with audio."""
    from moviepy import AudioFileClip
    from moviepy.audio.fx import AudioFadeOut

    print(f"\n  Building endcard ({ENDCARD_DURATION}s)...", flush=True)
    cover_path = broll_manifest.get("album_cover")
    endcard = build_end_card(album_data, cover_path, broll_manifest, broll_dir,
                             duration=ENDCARD_DURATION)

    # Attach #1 song audio
    if audio_path and os.path.exists(audio_path):
        try:
            full_audio = AudioFileClip(audio_path)
            peak_start = find_peak_segment(full_audio, ENDCARD_DURATION)
            ec_audio = full_audio.subclipped(peak_start, peak_start + ENDCARD_DURATION)
            ec_audio = ec_audio.with_effects([AudioFadeOut(AUDIO_FADEOUT)])
            endcard = endcard.with_audio(ec_audio)
            print(f"  Audio attached: peak segment from {peak_start:.1f}s", flush=True)
        except Exception as e:
            print(f"  Warning: Could not attach audio: {e}", flush=True)

    # Render
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    render_fps = 15 if draft else FPS
    render_bitrate = "2000k" if draft else "5000k"
    render_preset = "ultrafast" if draft else "medium"

    if draft:
        print(f"  DRAFT MODE: ultrafast, 15fps", flush=True)

    print(f"  Rendering to {output_path}...", flush=True)
    endcard.write_videofile(
        output_path,
        fps=render_fps,
        codec="libx264",
        audio_codec="aac",
        bitrate=render_bitrate,
        preset=render_preset,
        threads=os.cpu_count(),
        logger="bar",
    )

    file_size = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  Output: {output_path} ({file_size:.1f} MB)", flush=True)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate a 15s endcard clip for an album")
    parser.add_argument("album_md", help="Path to album markdown file (e.g. albums/1-CollegeDropout.md)")
    parser.add_argument("--output", "-o", help="Output path (default: data/endcards/<slug>.mp4)")
    parser.add_argument("--draft", action="store_true", help="Fast draft render (lower quality)")
    args = parser.parse_args()

    if not os.path.exists(args.album_md):
        print(f"Error: Album file not found: {args.album_md}")
        sys.exit(1)

    start_time = time.time()
    slug = get_slug(args.album_md)
    output_path = args.output or os.path.join(PROJECT_ROOT, "outputs", "endcards", f"{slug}.mp4")
    asset_dir = os.path.join(PROJECT_ROOT, ".tmp", "endcard_assets", slug)

    print(f"Generating endcard for: {slug}")
    print(f"Output: {output_path}")

    # 1. Parse album
    print(f"\n[1/4] Parsing album...")
    album_data = parse_album_markdown(args.album_md)
    print(f"  {album_data['album']} by {album_data['artist']} ({album_data['total_songs']} songs)")

    # 2. Download B-Roll
    print(f"\n[2/4] Preparing B-Roll...")
    broll_manifest = ensure_broll(album_data, asset_dir)

    # 3. Download #1 song audio
    print(f"\n[3/4] Preparing audio...")
    audio_path = ensure_audio(album_data, asset_dir)

    # 4. Render
    print(f"\n[4/4] Rendering endcard...")
    render_endcard(album_data, broll_manifest, os.path.join(asset_dir, "broll"),
                   audio_path, output_path, draft=args.draft)

    elapsed = time.time() - start_time
    print(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
