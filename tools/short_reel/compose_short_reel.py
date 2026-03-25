"""
Compose the short-format 9:16 Instagram reel (top 5 songs).

Uses the full reel's title card and end card with the top 5 songs, ~25-30s total.

Usage: python tools/short_reel/compose_short_reel.py [--album .tmp/album_data.json]
                                                      [--timing .tmp/short_timing.json]
                                                      [--audio-dir .tmp/audio]
                                                      [--broll-dir .tmp/broll]
                                                      [--output .tmp/output/short_reel_final.mp4]
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
from full_reel.compose_full_reel import build_title_card


def generate_hook_text(album_data):
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


def build_hook_card(album_data, cover_path, broll_manifest=None, broll_dir=None,
                    audio_dir=None, audio_manifest=None, duration=2.5):
    """Build a scroll-stopping hook card: album cover, hook text, album name only."""
    album_name = album_data["album"]

    # --- Background: dimmed B-Roll video ---
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
                dark_overlay = ColorClip(size=(WIDTH, HEIGHT), color=(0, 0, 0)).with_duration(duration).with_opacity(0.65)
                bg_clip = CompositeVideoClip([bg_video, dark_overlay], size=(WIDTH, HEIGHT)).with_duration(duration)
            except Exception as e:
                print(f"  Warning: Hook card background failed: {e}")

    layers = [bg_clip]

    # --- Build all text/graphics with PIL ---
    card_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card_img)

    # Dynamic hook text — the scroll-stopper, positioned above the cover
    hook_text = generate_hook_text(album_data)
    font_hook = ImageFont.truetype(FONT_IMPACT, 72)
    wrapped_hook = wrap_text(hook_text, font_hook, WIDTH - 100)
    if "\n" in wrapped_hook:
        hook_bbox = draw.multiline_textbbox((0, 0), wrapped_hook, font=font_hook, stroke_width=5)
    else:
        hook_bbox = draw.textbbox((0, 0), wrapped_hook, font=font_hook, stroke_width=5)
    hook_w = hook_bbox[2] - hook_bbox[0]
    hook_h = hook_bbox[3] - hook_bbox[1]
    hook_x = (WIDTH - hook_w) // 2
    hook_y = 300
    hook_draw_method = draw.multiline_text if "\n" in wrapped_hook else draw.text
    hook_draw_method((hook_x, hook_y), wrapped_hook, font=font_hook, fill="#FFD700",
                     stroke_width=5, stroke_fill="black", align="center")

    # Album cover — large, centered
    cover_size = 540
    border_width = 6
    cover_y = hook_y + hook_h + 60
    cover_x = (WIDTH - cover_size) // 2

    if cover_path and os.path.exists(cover_path):
        try:
            cover_img = Image.open(cover_path).convert("RGBA")
            w, h = cover_img.size
            cs = min(w, h)
            cover_img = cover_img.crop(((w - cs) // 2, (h - cs) // 2, (w + cs) // 2, (h + cs) // 2))
            cover_img = cover_img.resize((cover_size, cover_size), Image.LANCZOS)

            border_rect = [
                cover_x - border_width, cover_y - border_width,
                cover_x + cover_size + border_width, cover_y + cover_size + border_width,
            ]
            draw.rectangle(border_rect, fill="white", outline="white")
            card_img.paste(cover_img, (cover_x, cover_y), cover_img)
        except Exception:
            pass

    # Album name — below cover
    font_album = ImageFont.truetype(FONT_IMPACT, 80)
    album_upper = album_name.upper()
    wrapped_album = wrap_text(album_upper, font_album, WIDTH - 100)
    if "\n" in wrapped_album:
        album_bbox = draw.multiline_textbbox((0, 0), wrapped_album, font=font_album, stroke_width=5)
    else:
        album_bbox = draw.textbbox((0, 0), wrapped_album, font=font_album, stroke_width=5)
    album_w = album_bbox[2] - album_bbox[0]
    album_x = (WIDTH - album_w) // 2
    album_y = cover_y + cover_size + 35
    draw_method = draw.multiline_text if "\n" in wrapped_album else draw.text
    draw_method((album_x, album_y), wrapped_album, font=font_album, fill="white",
                stroke_width=5, stroke_fill="black", align="center")

    # Convert PIL image to clip
    card_clip = ImageClip(np.array(card_img), transparent=True).with_duration(duration)
    layers.append(card_clip)

    hook_clip = CompositeVideoClip(layers, size=(WIDTH, HEIGHT)).with_duration(duration)

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
                hc_audio = full_audio.subclipped(start_time, end_time)
                from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
                hc_audio = hc_audio.with_effects([AudioFadeIn(0.2), AudioFadeOut(0.3)])
                hook_clip = hook_clip.with_audio(hc_audio)
                print(f"  Hook card audio: {rank1_song['name']}")
            except Exception as e:
                print(f"  Warning: Hook card audio failed: {e}")

    return hook_clip


def build_short_segment(segment, audio_dir, broll_dir, broll_manifest, audio_manifest, broll_assignment=None, fade_duration=0.4):
    """Build a single segment with bolder, larger text for the short reel format."""
    idx = segment["song_index"]
    duration = segment["duration"]
    song_name = segment["name"]
    rating = segment["rating"]
    countdown = segment["countdown_number"]

    # --- B-Roll ---
    if broll_assignment and broll_assignment.get("file"):
        try:
            broll_clip = VideoFileClip(broll_assignment["file"])
            broll_clip = crop_to_vertical(broll_clip)
            if broll_clip.duration < duration:
                broll_clip = broll_clip.looped(duration=duration)
            else:
                max_start = max(0, broll_clip.duration - duration)
                start = min(broll_assignment["start_offset"], max_start)
                broll_clip = broll_clip.subclipped(start, start + duration)
            broll_clip = broll_clip.without_audio()
        except Exception as e:
            print(f"  Warning: B-Roll failed for segment {idx}: {e}")
            broll_clip = ColorClip(size=(WIDTH, HEIGHT), color=(20, 20, 20)).with_duration(duration)
    else:
        broll_clip = ColorClip(size=(WIDTH, HEIGHT), color=(20, 20, 20)).with_duration(duration)

    # --- Audio ---
    audio_entry = audio_manifest[idx] if idx < len(audio_manifest) else None
    audio_clip = None
    if audio_entry and audio_entry["file"] is not None and os.path.exists(audio_entry["file"]):
        try:
            full_audio = AudioFileClip(audio_entry["file"])
            peak_start = find_peak_segment(full_audio, duration)
            end_time = min(peak_start + duration, full_audio.duration)
            start_time = max(0, end_time - duration)
            audio_clip = full_audio.subclipped(start_time, end_time)
        except Exception as e:
            print(f"  Warning: Audio failed for '{song_name}': {e}")

    # --- Text Overlays (larger + thicker stroke for short reel) ---
    text_layers = []

    # Countdown number — top-left, large
    countdown_text = f"#{countdown}"
    text_layers.extend(make_text_clip(
        countdown_text, fontsize=140, duration=duration,
        position=(40, 50),
        font_override=FONT_IMPACT,
    ))

    # Rating — top-right, colored
    rating_display = int(rating) if rating == int(rating) else rating
    rating_text = f"{rating_display}/10"
    text_layers.extend(make_text_clip(
        rating_text, fontsize=96, duration=duration,
        position=(WIDTH - 290, 70),
        color=rating_color(rating),
        font_override=FONT_IMPACT,
    ))

    # Song name — centered, significantly larger with heavier stroke
    name_fontsize = 110 if len(song_name) <= 15 else 96 if len(song_name) <= 20 else 80 if len(song_name) <= 30 else 64
    # Render song name with extra-thick stroke using PIL directly for more pop
    font_path = FONT_DISPLAY
    font = ImageFont.truetype(font_path, name_fontsize)
    max_width = WIDTH - 80
    bbox = font.getbbox(song_name)
    text_width = bbox[2] - bbox[0]
    if text_width > max_width:
        song_name_wrapped = wrap_text(song_name, font, max_width)
        while name_fontsize > 36:
            bbox = font.getbbox(song_name_wrapped.split("\n")[0])
            if bbox[2] - bbox[0] <= max_width:
                break
            name_fontsize -= 4
            font = ImageFont.truetype(font_path, name_fontsize)
            song_name_wrapped = wrap_text(song_name, font, max_width)
    else:
        song_name_wrapped = song_name

    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    stroke_w = 6  # thicker stroke for more pop
    if "\n" in song_name_wrapped:
        sbbox = tmp_draw.multiline_textbbox((0, 0), song_name_wrapped, font=font, stroke_width=stroke_w)
    else:
        sbbox = tmp_draw.textbbox((0, 0), song_name_wrapped, font=font, stroke_width=stroke_w)
    text_w = sbbox[2] - sbbox[0] + 20
    text_h = sbbox[3] - sbbox[1] + 20

    img = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_method = draw.multiline_text if "\n" in song_name_wrapped else draw.text
    draw_method(
        (10 - sbbox[0], 10 - sbbox[1]),
        song_name_wrapped,
        font=font,
        fill="white",
        stroke_width=stroke_w,
        stroke_fill="black",
    )

    name_clip = ImageClip(np.array(img), transparent=True).with_duration(duration)
    px = (WIDTH - text_w) // 2
    py = HEIGHT // 2 - 50
    name_clip = name_clip.with_position((px, py))
    text_layers.append(name_clip)

    # Compose segment
    segment_clip = CompositeVideoClip(
        [broll_clip] + text_layers,
        size=(WIDTH, HEIGHT),
    ).with_duration(duration)

    if audio_clip is not None:
        segment_clip = segment_clip.with_audio(audio_clip)

    if fade_duration > 0:
        from moviepy.video.fx import CrossFadeIn, CrossFadeOut
        from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
        segment_clip = segment_clip.with_effects([
            CrossFadeIn(fade_duration),
            CrossFadeOut(fade_duration),
        ])
        if segment_clip.audio is not None:
            segment_clip.audio = segment_clip.audio.with_effects([
                AudioFadeIn(fade_duration),
                AudioFadeOut(fade_duration),
            ])

    return segment_clip


def build_short_end_card(album_data, cover_path, broll_manifest, broll_dir, duration=5.0):
    """Build end card with 'FULL RANKING ON PAGE' text at the bottom."""
    # Build the standard end card
    end_card_base = build_end_card(album_data, cover_path, broll_manifest, broll_dir, duration=duration)

    # Add "FULL RANKING ON PAGE" text overlay at the bottom
    cta_layers = make_text_clip(
        "FULL RANKING ON PAGE", fontsize=52, duration=duration,
        position=("center", HEIGHT - 140),
        color="#FFD700",
        font_override=FONT_IMPACT,
    )

    return CompositeVideoClip(
        [end_card_base] + cta_layers,
        size=(WIDTH, HEIGHT),
    ).with_duration(duration)


def main():
    parser = argparse.ArgumentParser(description="Compose short-format Instagram reel")
    parser.add_argument("--album", default=".tmp/album_data.json")
    parser.add_argument("--timing", default=".tmp/short_timing.json")
    parser.add_argument("--audio-dir", default=".tmp/audio")
    parser.add_argument("--broll-dir", default=".tmp/broll")
    parser.add_argument("--output", default=".tmp/output/short_reel_final.mp4")
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
    print(f"Composing short reel: {len(segments)} segments")
    print(f"Album: {album_data['album']} by {album_data['artist']}")

    # Build title card (same as full reel)
    cover_path = None
    if isinstance(broll_manifest, dict):
        cover_path = broll_manifest.get("album_cover")
    print(f"Building title card...")
    title_card = build_title_card(album_data, cover_path, broll_manifest, args.broll_dir,
                                  audio_dir=args.audio_dir, audio_manifest=audio_manifest, duration=3.0)
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
        if countdown == 1:
            fade = 1.5  # #1 reveal gets dramatic long fade
        elif countdown <= 3:
            fade = 0.8  # top 3 get slightly longer fades
        else:
            fade = 0.3  # quick fades for bottom songs (short reel = snappy)

        clip = build_short_segment(
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

    # Build end card (same as full reel)
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
            ec_audio = ec_audio.with_effects([AudioFadeOut(1.5)])
            end_card = end_card.with_audio(ec_audio)
            print(f"  #1 song audio continues into end card")
        except Exception as e:
            print(f"  Warning: Could not attach audio to end card: {e}")

    video_segments.append(end_card)

    # Concatenate with crossfade
    crossfade = 0.3  # tighter crossfade for short reel
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
    final_duration = final.duration
    print(f"\nDone! Output: {args.output}")
    print(f"  Duration: {final_duration:.1f}s | Size: {file_size:.1f} MB | Time: {total_time:.0f}s")


if __name__ == "__main__":
    main()
