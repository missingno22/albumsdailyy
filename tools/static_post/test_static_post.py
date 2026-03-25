"""
Render both static post slides for quick visual testing.

Usage: python tools/static_post/test_static_post.py [--album .tmp/album_data.json]
                                                      [--broll-dir .tmp/broll]
                                                      [--output-dir .tmp/output]

Output: .tmp/output/post_slide_1.png, .tmp/output/post_slide_2.png
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from static_post.compose_static_post import build_title_slide, build_ratings_slide


def main():
    parser = argparse.ArgumentParser(description="Test static post render")
    parser.add_argument("--album", default=".tmp/album_data.json")
    parser.add_argument("--broll-dir", default=".tmp/broll")
    parser.add_argument("--output-dir", default=".tmp/output")
    args = parser.parse_args()

    if not os.path.exists(args.album):
        print(f"Error: Album data not found: {args.album}")
        sys.exit(1)

    with open(args.album, "r", encoding="utf-8") as f:
        album_data = json.load(f)

    cover_path = None
    broll_manifest_path = os.path.join(args.broll_dir, "manifest.json")
    if os.path.exists(broll_manifest_path):
        with open(broll_manifest_path, "r", encoding="utf-8") as f:
            broll_manifest = json.load(f)
        if isinstance(broll_manifest, dict):
            cover_path = broll_manifest.get("album_cover")

    print(f"Testing static post for: {album_data['album']} by {album_data['artist']}")

    os.makedirs(args.output_dir, exist_ok=True)
    start = time.time()

    slide1 = build_title_slide(album_data, cover_path)
    slide1_path = os.path.join(args.output_dir, "post_slide_1.png")
    slide1.save(slide1_path, "PNG")
    print(f"  Slide 1: {slide1_path} ({os.path.getsize(slide1_path) / 1024:.0f} KB)")

    slide2 = build_ratings_slide(album_data, cover_path)
    slide2_path = os.path.join(args.output_dir, "post_slide_2.png")
    slide2.save(slide2_path, "PNG")
    print(f"  Slide 2: {slide2_path} ({os.path.getsize(slide2_path) / 1024:.0f} KB)")

    elapsed = time.time() - start
    print(f"\nDone! Both slides generated in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
