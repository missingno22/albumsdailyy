"""
Generate a lightweight 15-second endcard clip for an album.

Usage:
    python tools/generate_endcard_lite.py albums/1-CollegeDropout.md
    python tools/generate_endcard_lite.py albums/1-CollegeDropout.md --output data/endcards/1-CollegeDropout.mp4
    python tools/generate_endcard_lite.py albums/1-CollegeDropout.md --draft

Downloads only what's needed:
  - 1 B-Roll clip (music video for #1 song) for blurred background
  - 1 audio track (#1 song) with peak detection + fadeout
  - Album cover thumbnail
No full-album B-Roll downloads. Much faster than generate_endcard.py.
"""

import argparse
import glob
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from parse_markdown import parse_album_markdown
from shared.video_utils import build_end_card, find_peak_segment, FPS


ENDCARD_DURATION = 15.0
AUDIO_FADEOUT = 3.0


def get_slug(album_path):
    return os.path.splitext(os.path.basename(album_path))[0]


def download_cover(album, artist, output_dir):
    """Download album cover thumbnail. Fast (~2-5s)."""
    os.makedirs(output_dir, exist_ok=True)
    cover_base = os.path.join(output_dir, "album_cover")

    # Check cache
    for ext in ["*.webp", "*.jpg", "*.png"]:
        matches = glob.glob(os.path.join(output_dir, f"album_cover{ext.lstrip('*')}"))
        if matches:
            print(f"  Cached: {matches[0]}", flush=True)
            return matches[0]

    query = f"{artist} {album} album cover"
    print(f"  Searching: {query}", flush=True)
    cmd = [
        "yt-dlp", f"ytsearch1:{query}",
        "--write-thumbnail", "--skip-download",
        "-o", cover_base,
        "--no-playlist", "--socket-timeout", "30",
        "--no-warnings", "-q",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        for ext in ["*.webp", "*.jpg", "*.png"]:
            matches = glob.glob(os.path.join(output_dir, f"album_cover{ext.lstrip('*')}"))
            if matches:
                print(f"  Saved: {matches[0]}", flush=True)
                return matches[0]
    except subprocess.TimeoutExpired:
        pass

    print(f"  Warning: Could not download cover", flush=True)
    return None


def download_single_broll(artist, song_name, output_dir):
    """Download one 15s B-Roll clip from the #1 song's music video."""
    os.makedirs(output_dir, exist_ok=True)
    clip_path = os.path.join(output_dir, "broll_bg.mp4")

    if os.path.exists(clip_path):
        print(f"  Cached: {clip_path}", flush=True)
        return clip_path

    query = f"{artist} {song_name} official music video"
    print(f"  Searching: {query}", flush=True)
    cmd = [
        "yt-dlp", f"ytsearch1:{query}",
        "-f", "bestvideo[height<=720][height>=360]+bestaudio/best[height<=720][height>=360]",
        "--download-sections", "*0:15-0:30",
        "--merge-output-format", "mp4",
        "-o", clip_path,
        "--no-playlist", "--socket-timeout", "30",
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
                print(f"    {line.rstrip()}", flush=True)
        process.wait(timeout=120)

        if os.path.exists(clip_path):
            print(f"  Saved: {clip_path}", flush=True)
            return clip_path
    except subprocess.TimeoutExpired:
        process.kill()
        print(f"  Download timed out", flush=True)

    print(f"  Warning: Could not download B-Roll clip", flush=True)
    return None


def download_single_audio(artist, song_name, output_dir):
    """Download audio for the #1 song."""
    os.makedirs(output_dir, exist_ok=True)
    safe_name = song_name.replace("/", "").replace("\\", "").replace(":", "").strip()
    audio_path = os.path.join(output_dir, f"top_song_{safe_name}.mp3")

    if os.path.exists(audio_path):
        print(f"  Cached: {audio_path}", flush=True)
        return audio_path

    queries = [
        f"{artist} {song_name} audio",
        f"{artist} {song_name}",
    ]
    for query in queries:
        print(f"  Searching: {query}", flush=True)
        cmd = [
            "yt-dlp", f"ytsearch1:{query}",
            "-x", "--audio-format", "mp3",
            "-o", audio_path,
            "--no-playlist", "--socket-timeout", "30",
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
                    print(f"    {line.rstrip()}", flush=True)
            process.wait(timeout=90)

            if os.path.exists(audio_path):
                print(f"  Saved: {audio_path}", flush=True)
                return audio_path
        except subprocess.TimeoutExpired:
            process.kill()
            print(f"  Timed out: {query}", flush=True)

    print(f"  Warning: Could not download audio for '{song_name}'", flush=True)
    return None


def main():
    parser = argparse.ArgumentParser(description="Generate a 15s endcard (lite: 1 clip + 1 audio)")
    parser.add_argument("album_md", help="Path to album markdown file")
    parser.add_argument("--output", "-o", help="Output path (default: data/endcards/<slug>.mp4)")
    parser.add_argument("--draft", action="store_true", help="Fast draft render (lower quality)")
    args = parser.parse_args()

    if not os.path.exists(args.album_md):
        print(f"Error: Album file not found: {args.album_md}")
        sys.exit(1)

    start_time = time.time()
    slug = get_slug(args.album_md)
    output_path = args.output or os.path.join(PROJECT_ROOT, "data", "endcards", f"{slug}.mp4")
    asset_dir = os.path.join(PROJECT_ROOT, ".tmp", "endcard_assets", slug)

    print(f"Generating endcard (lite) for: {slug}", flush=True)
    print(f"Output: {output_path}", flush=True)

    # 1. Parse album
    print(f"\n[1/5] Parsing album...", flush=True)
    album_data = parse_album_markdown(args.album_md)
    top_song = min(album_data["songs"], key=lambda s: s["rank"])
    print(f"  {album_data['album']} by {album_data['artist']} ({album_data['total_songs']} songs)", flush=True)
    print(f"  #1 song: {top_song['name']} ({top_song['rating']}/10)", flush=True)

    # 2. Album cover
    print(f"\n[2/5] Album cover...", flush=True)
    cover_path = download_cover(album_data["album"], album_data["artist"], asset_dir)

    # 3. One B-Roll clip (blurred background)
    print(f"\n[3/5] B-Roll background clip...", flush=True)
    broll_dir = os.path.join(asset_dir, "broll")
    broll_path = download_single_broll(album_data["artist"], top_song["name"], broll_dir)

    # Build manifest for build_end_card
    broll_manifest = {
        "clips": [{"file": broll_path, "song": top_song["name"]}] if broll_path else [],
        "album_cover": cover_path,
    }

    # 4. Audio for #1 song
    print(f"\n[4/5] Audio ({top_song['name']})...", flush=True)
    audio_dir = os.path.join(asset_dir, "audio")
    audio_path = download_single_audio(album_data["artist"], top_song["name"], audio_dir)

    # 5. Render
    print(f"\n[5/5] Rendering endcard ({ENDCARD_DURATION}s)...", flush=True)
    endcard = build_end_card(
        album_data,
        cover_path=cover_path,
        broll_manifest=broll_manifest,
        broll_dir=broll_dir,
        duration=ENDCARD_DURATION,
    )

    # Attach audio
    if audio_path and os.path.exists(audio_path):
        try:
            from moviepy import AudioFileClip
            from moviepy.audio.fx import AudioFadeOut

            full_audio = AudioFileClip(audio_path)
            peak_start = find_peak_segment(full_audio, ENDCARD_DURATION)
            ec_audio = full_audio.subclipped(peak_start, peak_start + ENDCARD_DURATION)
            ec_audio = ec_audio.with_effects([AudioFadeOut(AUDIO_FADEOUT)])
            endcard = endcard.with_audio(ec_audio)
            print(f"  Audio attached: peak from {peak_start:.1f}s", flush=True)
        except Exception as e:
            print(f"  Warning: Could not attach audio: {e}", flush=True)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    render_fps = 15 if args.draft else FPS
    render_bitrate = "2000k" if args.draft else "5000k"
    render_preset = "ultrafast" if args.draft else "medium"

    if args.draft:
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
    elapsed = time.time() - start_time
    print(f"\n  Output: {output_path} ({file_size:.1f} MB)", flush=True)
    print(f"Done in {elapsed:.0f}s", flush=True)


if __name__ == "__main__":
    main()
