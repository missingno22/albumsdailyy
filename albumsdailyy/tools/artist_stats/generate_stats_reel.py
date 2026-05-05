"""
Generate the Artist Stats REEL (1080x1920, ~12s).

Layout:
    - Background: blurred broll clip (looped) from the artist's most-streamed song
      with a dark overlay for legibility.
    - Foreground: the static stats grid (rendered by generate_stats_post.render_grid),
      vertically centered on the canvas.
    - Audio: peak segment of the most-streamed track with fade-out.

Usage:
    python -m tools.artist_stats.generate_stats_reel "Kanye West" lowest_streamed_per_album
    python -m tools.artist_stats.generate_stats_reel "Kanye West" lowest_streamed_per_album --output out.mp4
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))

from albumsdailyy.tools.artist_stats import stats_registry
from albumsdailyy.tools.artist_stats.generate_stats_post import (
    render_grid, POST_W, POST_H, REEL_W, REEL_H,
)
from albumsdailyy.tools.generate_endcard_lite import (
    download_single_broll, download_single_audio,
)
from albumsdailyy.tools.shared.video_utils import (
    crop_to_vertical, find_peak_segment, _blur_frame, FPS,
)


REEL_DURATION = 12.0
AUDIO_FADEOUT = 2.5


def _pick_top_track(artist_data):
    """Pick the artist's overall most-streamed track (used for broll + audio)."""
    best = None
    for album in artist_data["albums"]:
        for t in album["tracks"]:
            if t.get("streams") is None:
                continue
            if best is None or t["streams"] > best["streams"]:
                best = t
    return best


def build_reel(artist_data, stat_data, output_path, draft=False):
    """Compose the reel: blurred broll bg + grid overlay + audio."""
    from moviepy import (
        VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, ColorClip,
    )
    from moviepy.audio.fx import AudioFadeOut

    asset_dir = os.path.join(
        PROJECT_ROOT, ".tmp", "stats_reel_assets",
        artist_data["name"].replace(" ", "_"),
    )
    os.makedirs(asset_dir, exist_ok=True)

    # ---- 1. Pick a top track for broll + audio ----
    top = _pick_top_track(artist_data)
    if top is None:
        raise RuntimeError(f"No streamable track found for {artist_data['name']}")
    print(f"  [reel] anchor track: {top['name']} ({top['streams']:,} streams)", flush=True)

    # ---- 2. Background broll ----
    print(f"\n[1/4] Background broll...", flush=True)
    broll_path = download_single_broll(
        artist_data["name"], top["name"],
        os.path.join(asset_dir, "broll"),
    )

    bg = ColorClip(size=(REEL_W, REEL_H), color=(15, 15, 15)).with_duration(REEL_DURATION)
    if broll_path and os.path.exists(broll_path):
        try:
            bg_video = VideoFileClip(broll_path)
            bg_video = crop_to_vertical(bg_video)
            if bg_video.duration < REEL_DURATION:
                bg_video = bg_video.looped(duration=REEL_DURATION)
            else:
                bg_video = bg_video.subclipped(0, REEL_DURATION)
            bg_video = bg_video.without_audio()
            bg_video = bg_video.image_transform(lambda f: _blur_frame(f, radius=25))
            # Lighter dark overlay (was 0.65) so broll bleeds through the grid
            dark = ColorClip(size=(REEL_W, REEL_H), color=(0, 0, 0))\
                .with_duration(REEL_DURATION).with_opacity(0.35)
            bg = CompositeVideoClip([bg_video, dark], size=(REEL_W, REEL_H))\
                .with_duration(REEL_DURATION)
        except Exception as e:
            print(f"  [reel] WARNING: broll background failed: {e}", flush=True)

    # ---- 3. Foreground: stats grid as PNG overlay ----
    print(f"\n[2/4] Rendering grid overlay...", flush=True)
    # Transparent grid so broll bleeds through everything except covers/text/pills
    grid_img = render_grid(stat_data, artist_data["name"],
                           canvas_w=POST_W, canvas_h=POST_H, bg=None)
    grid_rgba = grid_img if grid_img.mode == "RGBA" else grid_img.convert("RGBA")

    # Add a semi-transparent dark scrim BEHIND just the grid (boxed) for legibility,
    # so cover labels stay readable but broll still shows through.
    scrim = Image.new("RGBA", (POST_W, POST_H), (10, 10, 10, 120))  # ~47% alpha
    panel = Image.alpha_composite(scrim, grid_rgba)
    grid_arr = np.array(panel)

    grid_clip = ImageClip(grid_arr, transparent=True).with_duration(REEL_DURATION)
    grid_y = (REEL_H - POST_H) // 2
    grid_clip = grid_clip.with_position((0, grid_y))

    # ---- 4. Compose video ----
    print(f"\n[3/4] Compositing...", flush=True)
    video = CompositeVideoClip([bg, grid_clip], size=(REEL_W, REEL_H))\
        .with_duration(REEL_DURATION)

    # ---- 5. Audio ----
    print(f"\n[4/4] Audio...", flush=True)
    audio_path = download_single_audio(
        artist_data["name"], top["name"],
        os.path.join(asset_dir, "audio"),
    )
    if audio_path and os.path.exists(audio_path):
        try:
            full_audio = AudioFileClip(audio_path)
            peak_start = find_peak_segment(full_audio, REEL_DURATION)
            audio_clip = full_audio.subclipped(peak_start, peak_start + REEL_DURATION)
            audio_clip = audio_clip.with_effects([AudioFadeOut(AUDIO_FADEOUT)])
            video = video.with_audio(audio_clip)
            print(f"  [reel] audio attached: peak from {peak_start:.1f}s", flush=True)
        except Exception as e:
            print(f"  [reel] WARNING: audio attach failed: {e}", flush=True)

    # ---- 6. Render ----
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fps = 15 if draft else FPS
    bitrate = "2500k" if draft else "5000k"
    preset = "ultrafast" if draft else "medium"
    print(f"\n  Writing -> {output_path} (fps={fps}, preset={preset})", flush=True)
    video.write_videofile(
        output_path,
        fps=fps, codec="libx264", audio_codec="aac",
        bitrate=bitrate, preset=preset,
        threads=os.cpu_count(), logger="bar",
    )
    return output_path


def main():
    p = argparse.ArgumentParser(description="Generate an artist stats reel (1080x1920, ~12s)")
    p.add_argument("artist", help="Artist name")
    p.add_argument("stat_type", choices=list(stats_registry.STAT_TYPES))
    p.add_argument("--output", "-o", help="Output mp4 path")
    p.add_argument("--draft", action="store_true", help="Fast/lower-quality render")
    p.add_argument("--refresh", action="store_true", help="Force refresh of kworb cache")
    args = p.parse_args()

    data = stats_registry.build_artist_data(args.artist, force_refresh=args.refresh)
    stat = stats_registry.build_stat(data, args.stat_type)
    print(f"\n[stat] {stat['title']} — {len(stat['items'])} items", flush=True)

    output = args.output or os.path.join(
        PROJECT_ROOT, "outputs", "stats_reels",
        f"{data['name'].replace(' ', '_')}_{args.stat_type}.mp4",
    )
    build_reel(data, stat, output, draft=args.draft)
    size_mb = os.path.getsize(output) / (1024 * 1024)
    print(f"\n[done] {output} ({size_mb:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
