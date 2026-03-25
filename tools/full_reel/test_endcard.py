"""
Render just the end card for quick visual testing.

Usage: python tools/full_reel/test_endcard.py [--album .tmp/album_data.json] [--duration 10]

Output: .tmp/output/endcard_test.mp4
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared import build_end_card


def main():
    parser = argparse.ArgumentParser(description="Test end card render")
    parser.add_argument("--album", default=".tmp/album_data.json")
    parser.add_argument("--broll-dir", default=".tmp/broll")
    parser.add_argument("--output", default=".tmp/output/endcard_test.mp4")
    parser.add_argument("--duration", type=float, default=10.0)
    args = parser.parse_args()

    if not os.path.exists(args.album):
        print(f"Error: Album data not found: {args.album}")
        sys.exit(1)

    with open(args.album, "r", encoding="utf-8") as f:
        album_data = json.load(f)

    broll_manifest = []
    broll_manifest_path = os.path.join(args.broll_dir, "manifest.json")
    if os.path.exists(broll_manifest_path):
        with open(broll_manifest_path, "r", encoding="utf-8") as f:
            broll_manifest = json.load(f)

    cover_path = None
    if isinstance(broll_manifest, dict):
        cover_path = broll_manifest.get("album_cover")

    print(f"Building end card for: {album_data['album']} by {album_data['artist']}")
    print(f"  {len(album_data['songs'])} songs, duration: {args.duration}s")

    start = time.time()
    end_card = build_end_card(album_data, cover_path, broll_manifest, args.broll_dir, duration=args.duration)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    print(f"Rendering to {args.output} (draft quality)...")
    end_card.write_videofile(
        args.output,
        fps=15,
        codec="libx264",
        audio_codec="aac",
        bitrate="2000k",
        preset="ultrafast",
        threads=os.cpu_count(),
        logger="bar",
    )

    elapsed = time.time() - start
    file_size = os.path.getsize(args.output) / (1024 * 1024)
    print(f"\nDone! {args.output} ({file_size:.1f} MB) in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
