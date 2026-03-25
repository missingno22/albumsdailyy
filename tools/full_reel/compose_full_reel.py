"""
Compose the full-length 9:16 Instagram reel from audio, B-Roll, and timing data.

This is the "every song rated" format — all songs, worst to best, ~60-90s total.

Usage: python tools/full_reel/compose_full_reel.py [--album .tmp/album_data.json]
                                                    [--timing .tmp/timing.json]
                                                    [--audio-dir .tmp/audio]
                                                    [--broll-dir .tmp/broll]
                                                    [--output .tmp/output/reel_final.mp4]
"""

import json
import os
import sys
import argparse
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    VideoFileClip,
    AudioFileClip,
    ImageClip,
    CompositeVideoClip,
    ColorClip,
    concatenate_videoclips,
)

# Add tools directory to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared import (
    WIDTH, HEIGHT, FPS,
    FONT_BOLD, FONT_REGULAR, FONT_IMPACT, FONT_DISPLAY,
    rating_color, find_peak_segment, crop_to_vertical,
    wrap_text, make_text_clip, plan_broll_assignments,
    build_end_card, build_segment,
)


def _blur_frame(frame, radius=20):
    """Apply gaussian blur to a video frame using PIL."""
    from PIL import ImageFilter
    img = Image.fromarray(frame)
    blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.array(blurred)


def _ease_out_bounce(t):
    """Easing function: fast start, decelerates with a slight overshoot settle."""
    if t < 0:
        return 0.0
    if t >= 1:
        return 1.0
    # Overshoot then settle (like a slot reel stopping)
    t2 = t * t
    return 1.0 - (1.0 - t) ** 3 + 0.1 * np.sin(t * np.pi * 2) * (1.0 - t)


def _render_text_image(text, font_path, fontsize, fill, stroke_width=4, stroke_fill="black"):
    """Render text to a transparent RGBA PIL image, return (image, width, height)."""
    font = ImageFont.truetype(font_path, fontsize)
    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)

    max_width = WIDTH - 100
    bbox = tmp_draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    text_w = bbox[2] - bbox[0]
    if text_w > max_width:
        text = wrap_text(text, font, max_width)
        while fontsize > 28:
            bbox = font.getbbox(text.split("\n")[0])
            if bbox[2] - bbox[0] <= max_width:
                break
            fontsize -= 4
            font = ImageFont.truetype(font_path, fontsize)
            text = wrap_text(text.replace("\n", " "), font, max_width)

    if "\n" in text:
        bbox = tmp_draw.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    else:
        bbox = tmp_draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)

    img_w = bbox[2] - bbox[0] + 16
    img_h = bbox[3] - bbox[1] + 16

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_method = draw.multiline_text if "\n" in text else draw.text
    draw_method((8 - bbox[0], 8 - bbox[1]), text, font=font, fill=fill,
                stroke_width=stroke_width, stroke_fill=stroke_fill, align="center")

    return img, img_w, img_h


def _generate_title_hook(album_data):
    """Generate provocative hook text based on average album rating."""
    artist = album_data.get("artist", "THIS")
    songs = album_data["songs"]
    avg_rating = sum(s["rating"] for s in songs) / len(songs)

    if avg_rating >= 8:
        return f"BEST {artist.upper()} ALBUM?"
    elif avg_rating >= 6:
        return "EVERYONE GETS THIS RANKING WRONG"
    else:
        return f"WORST {artist.upper()} ALBUM?"


def build_title_card(album_data, cover_path, broll_manifest=None, broll_dir=None,
                     audio_dir=None, audio_manifest=None, duration=3.0):
    """Build the full reel title card: blurred B-Roll background, hook text + album name with slot-machine roll-in."""
    album_name = album_data["album"]
    artist_name = album_data["artist"]

    # --- Background: blurred & dimmed B-Roll video ---
    bg_clip = ColorClip(size=(WIDTH, HEIGHT), color=(15, 15, 15)).with_duration(duration)
    if broll_manifest and isinstance(broll_manifest, dict):
        clips = broll_manifest.get("clips", [])
        available = [c for c in clips if c.get("file") and os.path.exists(c["file"])]
        if available:
            try:
                pick = available[-1]
                bg_video = VideoFileClip(pick["file"])
                bg_video = crop_to_vertical(bg_video)
                if bg_video.duration < duration:
                    bg_video = bg_video.looped(duration=duration)
                else:
                    bg_video = bg_video.subclipped(0, duration)
                bg_video = bg_video.without_audio()
                # Apply heavy gaussian blur
                bg_video = bg_video.image_transform(lambda frame: _blur_frame(frame, radius=25))
                dark_overlay = ColorClip(size=(WIDTH, HEIGHT), color=(0, 0, 0)).with_duration(duration).with_opacity(0.6)
                bg_clip = CompositeVideoClip([bg_video, dark_overlay], size=(WIDTH, HEIGHT)).with_duration(duration)
            except Exception as e:
                print(f"  Warning: Title card background failed: {e}")

    layers = [bg_clip]

    # --- Render hook text and album identification as separate animated clips ---
    # Hook text (provocative scroll-stopper)
    hook_text = _generate_title_hook(album_data)
    title_img, title_w, title_h = _render_text_image(
        hook_text, FONT_IMPACT, 90, fill="#FFD700", stroke_width=5,
    )
    title_final_x = (WIDTH - title_w) // 2
    title_final_y = (HEIGHT // 2) - title_h - 15  # centered pair, hook above midpoint

    # Album identification (album name + artist)
    album_label = f"{album_name} — {artist_name}"
    artist_img, artist_w, artist_h = _render_text_image(
        album_label, FONT_DISPLAY, 52, fill="#CCCCCC", stroke_width=3,
    )
    artist_final_x = (WIDTH - artist_w) // 2
    artist_final_y = (HEIGHT // 2) + 15  # just below midpoint

    # Slot machine roll-in animation parameters
    roll_distance = 600  # pixels to travel from below
    title_roll_duration = 0.6  # seconds for title to roll in
    artist_roll_delay = 0.15  # artist starts slightly after title
    artist_roll_duration = 0.6

    # Title clip with roll-in position
    title_clip = ImageClip(np.array(title_img), transparent=True).with_duration(duration)
    def title_pos(t):
        progress = min(1.0, t / title_roll_duration)
        ease = _ease_out_bounce(progress)
        y_offset = roll_distance * (1.0 - ease)
        return (title_final_x, int(title_final_y + y_offset))
    title_clip = title_clip.with_position(title_pos)

    # Artist clip with delayed roll-in
    artist_clip = ImageClip(np.array(artist_img), transparent=True).with_duration(duration)
    def artist_pos(t):
        t_adj = t - artist_roll_delay
        if t_adj < 0:
            return (artist_final_x, artist_final_y + roll_distance)
        progress = min(1.0, t_adj / artist_roll_duration)
        ease = _ease_out_bounce(progress)
        y_offset = roll_distance * (1.0 - ease)
        return (artist_final_x, int(artist_final_y + y_offset))
    artist_clip = artist_clip.with_position(artist_pos)

    layers.append(title_clip)
    layers.append(artist_clip)

    title_card = CompositeVideoClip(layers, size=(WIDTH, HEIGHT)).with_duration(duration)

    # --- Audio: use the most popular song (rank 1) ---
    if audio_manifest and audio_dir:
        songs = album_data["songs"]
        rank1_song = None
        for s in songs:
            if s["rank"] == 1:
                rank1_song = s
                break
        if not rank1_song:
            rank1_song = songs[-1]

        rank1_idx = songs.index(rank1_song)
        audio_entry = audio_manifest[rank1_idx] if rank1_idx < len(audio_manifest) else None

        if audio_entry and audio_entry.get("file") and os.path.exists(audio_entry["file"]):
            try:
                full_audio = AudioFileClip(audio_entry["file"])
                peak_start = find_peak_segment(full_audio, duration)
                end_time = min(peak_start + duration, full_audio.duration)
                start_time = max(0, end_time - duration)
                tc_audio = full_audio.subclipped(start_time, end_time)
                from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
                tc_audio = tc_audio.with_effects([AudioFadeIn(0.3), AudioFadeOut(0.5)])
                title_card = title_card.with_audio(tc_audio)
                print(f"  Title card audio: {rank1_song['name']}")
            except Exception as e:
                print(f"  Warning: Title card audio failed: {e}")

    return title_card


def main():
    parser = argparse.ArgumentParser(description="Compose full-length Instagram reel")
    parser.add_argument("--album", default=".tmp/album_data.json")
    parser.add_argument("--timing", default=".tmp/timing.json")
    parser.add_argument("--audio-dir", default=".tmp/audio")
    parser.add_argument("--broll-dir", default=".tmp/broll")
    parser.add_argument("--output", default=".tmp/output/reel_final.mp4")
    parser.add_argument("--draft", action="store_true", help="Fast draft render (lower quality, much faster)")
    args = parser.parse_args()

    for path in [args.album, args.timing]:
        if not os.path.exists(path):
            print(f"Error: File not found: {path}")
            sys.exit(1)

    with open(args.album, "r", encoding="utf-8") as f:
        album_data = json.load(f)
    with open(args.timing, "r", encoding="utf-8") as f:
        timing_data = json.load(f)

    audio_manifest_path = os.path.join(args.audio_dir, "manifest.json")
    broll_manifest_path = os.path.join(args.broll_dir, "manifest.json")

    audio_manifest = []
    broll_manifest = []

    if os.path.exists(audio_manifest_path):
        with open(audio_manifest_path, "r", encoding="utf-8") as f:
            audio_manifest = json.load(f)

    if os.path.exists(broll_manifest_path):
        with open(broll_manifest_path, "r", encoding="utf-8") as f:
            broll_manifest = json.load(f)

    segments = timing_data["segments"]
    print(f"Composing full reel: {len(segments)} segments, {timing_data['total_duration']}s total")
    print(f"Album: {album_data['album']} by {album_data['artist']}")

    # Build title card
    cover_path = None
    if isinstance(broll_manifest, dict):
        cover_path = broll_manifest.get("album_cover")
    print(f"Building title card...")
    title_card = build_title_card(album_data, cover_path, broll_manifest, args.broll_dir,
                                  audio_dir=args.audio_dir, audio_manifest=audio_manifest)
    from moviepy.video.fx import FadeIn, FadeOut
    title_card = title_card.with_effects([FadeIn(0.5), FadeOut(0.5)])

    # Pre-plan B-Roll assignments
    broll_assignments = plan_broll_assignments(segments, broll_manifest)

    video_segments = [title_card]
    total_segments = len(segments)
    pipeline_start = time.time()

    for i, segment in enumerate(segments):
        seg_start = time.time()

        countdown = segment["countdown_number"]
        if countdown <= 3:
            fade = 1.2
        else:
            fade = 0.4

        clip = build_segment(
            segment, args.audio_dir, args.broll_dir, broll_manifest, audio_manifest,
            broll_assignment=broll_assignments[i],
            fade_duration=fade,
        )
        video_segments.append(clip)

        seg_elapsed = time.time() - seg_start
        total_elapsed = time.time() - pipeline_start
        avg_per = total_elapsed / (i + 1)
        remaining = avg_per * (total_segments - i - 1)
        pct = (i + 1) / total_segments * 100
        print(f"  [{i+1}/{total_segments}] Built #{segment['countdown_number']} "
              f"{segment['name']} ({segment['duration']}s) "
              f"in {seg_elapsed:.1f}s | ETA: {remaining:.0f}s remaining ({pct:.0f}%)")

    # Build end card
    print(f"Building end card...")
    end_card_duration = 6.0
    end_card = build_end_card(album_data, cover_path, broll_manifest, args.broll_dir, duration=end_card_duration)
    from moviepy.video.fx import FadeIn, FadeOut
    from moviepy.audio.fx import AudioFadeOut
    end_card = end_card.with_effects([FadeIn(1.0), FadeOut(0.5)])

    # Attach #1 song audio to end card
    last_seg = segments[-1]
    last_idx = last_seg["song_index"]
    last_audio_entry = audio_manifest[last_idx] if last_idx < len(audio_manifest) else None
    if last_audio_entry and last_audio_entry["file"] and os.path.exists(last_audio_entry["file"]):
        try:
            full_audio = AudioFileClip(last_audio_entry["file"])
            peak_start = find_peak_segment(full_audio, last_seg["duration"])
            continue_from = min(peak_start + last_seg["duration"], full_audio.duration)
            remaining_audio = full_audio.duration - continue_from
            if remaining_audio >= end_card_duration:
                ec_audio = full_audio.subclipped(continue_from, continue_from + end_card_duration)
            else:
                ec_start = max(0, full_audio.duration - end_card_duration)
                ec_audio = full_audio.subclipped(ec_start, full_audio.duration)
            ec_audio = ec_audio.with_effects([AudioFadeOut(2.0)])
            end_card = end_card.with_audio(ec_audio)
            print(f"  #1 song audio continues into end card")
        except Exception as e:
            print(f"  Warning: Could not attach audio to end card: {e}")

    video_segments.append(end_card)

    # Concatenate
    crossfade = 0.4
    print(f"\nConcatenating with {crossfade}s crossfade overlap...")

    if len(video_segments) > 1:
        final = concatenate_videoclips(video_segments, method="compose", padding=-crossfade)
    else:
        final = video_segments[0]

    # Render
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    print(f"\nRendering to {args.output}...")
    print(f"Using {os.cpu_count()} threads for encoding...")
    render_fps = 15 if args.draft else FPS
    render_bitrate = "2000k" if args.draft else "5000k"
    render_preset = "ultrafast" if args.draft else "medium"
    if args.draft:
        print("  DRAFT MODE: ultrafast preset, 15fps, 2000k bitrate")
    final.write_videofile(
        args.output,
        fps=render_fps,
        codec="libx264",
        audio_codec="aac",
        bitrate=render_bitrate,
        preset=render_preset,
        threads=os.cpu_count(),
        logger="bar",
    )

    total_time = time.time() - pipeline_start
    file_size = os.path.getsize(args.output) / (1024 * 1024)
    print(f"\nDone! Output: {args.output} ({file_size:.1f} MB)")
    print(f"Total time: {total_time:.0f}s")


if __name__ == "__main__":
    main()
