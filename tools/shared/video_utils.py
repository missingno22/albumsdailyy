"""
Shared video utilities for Instagram reel composition.

Contains all common functions used by both full reel and short reel compositors:
- Constants (resolution, fonts)
- Color mapping
- Audio peak detection
- Video cropping/scaling
- Text rendering
- B-Roll assignment planning
- End card builder
- Segment builder
"""

import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    VideoFileClip,
    AudioFileClip,
    ImageClip,
    CompositeVideoClip,
    ColorClip,
)


# === Constants ===
WIDTH, HEIGHT = 1080, 1920
FPS = 30

# Font paths — cross-platform (Windows local fonts, Linux CI uses DejaVu as fallback)
_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
_FONTS_DIR = os.path.join(_PROJECT_ROOT, "fonts")


def _resolve_font(primary, fallbacks):
    """Return the first font path that exists."""
    for path in [primary] + fallbacks:
        if os.path.exists(path):
            return path
    return primary  # let PIL raise the error with the intended path


# Custom font (committed to repo)
FONT_IMPACT = _resolve_font(
    os.path.join(_FONTS_DIR, "CollegiateBlackFLF.ttf"),
    [os.path.join(_PROJECT_ROOT, ".tmp", "fonts", "CollegiateBlackFLF.ttf")]
)

# System fonts with Linux fallbacks
FONT_BOLD = _resolve_font("C:/Windows/Fonts/arialbd.ttf", [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
])
FONT_REGULAR = _resolve_font("C:/Windows/Fonts/arial.ttf", [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
])
FONT_DISPLAY = _resolve_font("C:/Windows/Fonts/bahnschrift.ttf", [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
])


def rating_color(rating):
    """Return color string based on rating value."""
    r = int(rating)
    if r >= 10:
        return "#FF00FF"  # Magenta — Perfect
    elif r == 9:
        return "#4169E1"  # Royal Blue — Amazing
    elif r >= 7:
        return "#00CC00"  # Green — Great
    elif r >= 5:
        return "#FFFF00"  # Yellow — Mid
    elif r >= 3:
        return "#FF8C00"  # Orange — Bad
    elif r >= 1:
        return "#FF0000"  # Red — Very Bad
    else:
        return "#000000"  # Black — Awful


RATING_LEGEND = [
    ("\U0001f7ea", "Perfect", "#FF00FF"),
    ("\U0001f7e6", "Amazing", "#4169E1"),
    ("\U0001f7e9", "Great", "#00CC00"),
    ("\U0001f7e8", "Mid", "#FFFF00"),
    ("\U0001f7e7", "Bad", "#FF8C00"),
    ("\U0001f7e5", "Very Bad", "#FF0000"),
    ("\u2b1b", "Awful", "#000000"),
]


def find_peak_segment(audio_clip, duration):
    """Find the highest-energy segment of the given duration in an audio clip."""
    if audio_clip.duration <= duration:
        return 0.0

    sample_rate = 22050
    total_samples = int(audio_clip.duration * sample_rate)
    window_samples = int(duration * sample_rate)

    if total_samples <= window_samples:
        return 0.0

    try:
        audio_array = audio_clip.to_soundarray(fps=sample_rate)
        if len(audio_array.shape) > 1:
            audio_array = audio_array.mean(axis=1)  # mono

        step = int(0.5 * sample_rate)
        best_score = 0.0
        best_start = 0
        total_len = len(audio_array)

        for start in range(0, total_len - window_samples, step):
            segment = audio_array[start : start + window_samples]
            energy = np.sqrt(np.mean(segment ** 2))

            # Position preference: bell curve centered at 35% of song
            pos = start / total_len
            position_weight = np.exp(-0.5 * ((pos - 0.35) / 0.15) ** 2)
            score = energy * position_weight

            if score > best_score:
                best_score = score
                best_start = start

        return best_start / sample_rate
    except Exception:
        return audio_clip.duration * 0.3


def crop_to_vertical(clip):
    """Crop a video clip to 9:16 aspect ratio (center crop)."""
    src_w, src_h = clip.size
    target_ratio = WIDTH / HEIGHT  # 0.5625

    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        x_offset = (src_w - new_w) // 2
        clip = clip.cropped(x1=x_offset, x2=x_offset + new_w)
    else:
        new_h = int(src_w / target_ratio)
        y_offset = (src_h - new_h) // 2
        clip = clip.cropped(y1=y_offset, y2=y_offset + new_h)

    return clip.resized((WIDTH, HEIGHT))


def wrap_text(text, font, max_width):
    """Word-wrap text to fit within max_width pixels. Returns wrapped string."""
    words = text.split()
    if not words:
        return text

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = current_line + " " + word
        bbox = font.getbbox(test_line)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return "\n".join(lines)


def make_text_clip(text, fontsize, duration, position, color="white", bold=True, font_override=None):
    """Create a text clip using PIL for fast rendering with stroke outline."""
    font_path = font_override or (FONT_BOLD if bold else FONT_REGULAR)
    font = ImageFont.truetype(font_path, fontsize)

    max_width = WIDTH - 80
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    if text_width > max_width:
        text = wrap_text(text, font, max_width)
        while fontsize > 28:
            bbox = font.getbbox(text.split("\n")[0])
            if bbox[2] - bbox[0] <= max_width:
                break
            fontsize -= 4
            font = ImageFont.truetype(font_path, fontsize)
            text = wrap_text(text.replace("\n", " "), font, max_width)

    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    if "\n" in text:
        bbox = tmp_draw.multiline_textbbox((0, 0), text, font=font, stroke_width=4)
    else:
        bbox = tmp_draw.textbbox((0, 0), text, font=font, stroke_width=4)
    text_w = bbox[2] - bbox[0] + 16
    text_h = bbox[3] - bbox[1] + 16

    img = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw_method = draw.multiline_text if "\n" in text else draw.text
    draw_method(
        (8 - bbox[0], 8 - bbox[1]),
        text,
        font=font,
        fill=color,
        stroke_width=4,
        stroke_fill="black",
    )

    clip = ImageClip(np.array(img), transparent=True)
    clip = clip.with_duration(duration)

    px, py = position
    if px == "center":
        px = (WIDTH - text_w) // 2
    if py == "center":
        py = (HEIGHT - text_h) // 2

    clip = clip.with_position((px, py))
    return [clip]


def plan_broll_assignments(segments, broll_manifest):
    """Pre-plan B-Roll assignments. Every song MUST get a video clip -- no static frames."""
    if isinstance(broll_manifest, dict):
        clips = broll_manifest.get("clips", [])
    else:
        clips = broll_manifest

    # Collect all available video files for fallback
    all_available = [c["file"] for c in clips if c.get("file") and os.path.exists(c["file"])]

    if not all_available:
        print("  WARNING: No B-Roll clips available at all!")
        return [{"file": None, "start_offset": 0.0} for _ in segments]

    assignments = []
    prev_file = None

    for i, seg in enumerate(segments):
        idx = seg["song_index"]

        # 1. Try the song's own clip
        file_path = None
        if idx < len(clips) and clips[idx].get("file") and os.path.exists(clips[idx]["file"]):
            file_path = clips[idx]["file"]

        # 2. Try neighboring clips (avoid same as previous)
        if not file_path:
            for offset in [1, -1, 2, -2, 3, -3, 4, -4]:
                neighbor = idx + offset
                if 0 <= neighbor < len(clips) and clips[neighbor].get("file"):
                    candidate = clips[neighbor]["file"]
                    if os.path.exists(candidate) and candidate != prev_file:
                        file_path = candidate
                        break

        # 3. Avoid back-to-back same clip
        if file_path == prev_file and len(all_available) > 1:
            for f in all_available:
                if f != prev_file:
                    file_path = f
                    break

        # 4. Last resort: cycle through all available clips (never leave None)
        if not file_path:
            file_path = all_available[i % len(all_available)]

        assignments.append({
            "file": file_path,
            "start_offset": 0.0,
        })
        prev_file = file_path

    return assignments


def _blur_frame(frame, radius=25):
    """Apply gaussian blur to a video frame using PIL."""
    from PIL import ImageFilter
    img = Image.fromarray(frame)
    blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.array(blurred)


def _ease_out_cubic(t):
    """Cubic ease-out: fast start, decelerates smoothly."""
    if t < 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 1.0 - (1.0 - t) ** 3


def _render_line_image(text, font, fill, stroke_width=2, stroke_fill="black"):
    """Render a single line of text to a transparent RGBA image."""
    tmp = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp)
    bbox = tmp_draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    w = bbox[2] - bbox[0] + 12
    h = bbox[3] - bbox[1] + 12
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((6 - bbox[0], 6 - bbox[1]), text, font=font, fill=fill,
              stroke_width=stroke_width, stroke_fill=stroke_fill)
    return img, w, h


def build_end_card(album_data, cover_path, broll_manifest, broll_dir, duration=6.0):
    """Build end card: cover top-left, title+artist+rating+legend to right, song list below with roll-in."""
    album_name = album_data["album"]
    artist_name = album_data["artist"]
    songs = album_data["songs"]
    display_songs = sorted(songs, key=lambda s: s["rank"])

    avg_rating = sum(s["rating"] for s in songs) / len(songs)
    avg_rating = round(avg_rating, 1)

    # --- Background: blurred & slightly brighter B-Roll ---
    bg_clip = ColorClip(size=(WIDTH, HEIGHT), color=(15, 15, 15)).with_duration(duration)
    if isinstance(broll_manifest, dict):
        clips = broll_manifest.get("clips", [])
    else:
        clips = broll_manifest
    available_clips = [c for c in clips if c.get("file") and os.path.exists(c["file"])]
    if available_clips:
        try:
            pick = available_clips[len(available_clips) // 2]
            bg_video = VideoFileClip(pick["file"])
            bg_video = crop_to_vertical(bg_video)
            if bg_video.duration < duration:
                bg_video = bg_video.looped(duration=duration)
            else:
                bg_video = bg_video.subclipped(0, duration)
            bg_video = bg_video.without_audio()
            bg_video = bg_video.image_transform(lambda frame: _blur_frame(frame, radius=25))
            dark_overlay = ColorClip(size=(WIDTH, HEIGHT), color=(0, 0, 0)).with_duration(duration).with_opacity(0.55)
            bg_clip = CompositeVideoClip([bg_video, dark_overlay], size=(WIDTH, HEIGHT)).with_duration(duration)
        except Exception as e:
            print(f"  Warning: End card background failed: {e}")

    layers = [bg_clip]

    # --- Static header: cover + title/artist/rating/legend (rendered as one PIL image) ---
    header_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(header_img)

    margin = 40
    top_padding = 60
    cover_size = 420
    cover_x = margin
    cover_y = top_padding

    # Album cover — top left
    if cover_path and os.path.exists(cover_path):
        try:
            cover_img = Image.open(cover_path).convert("RGBA")
            w, h = cover_img.size
            cs = min(w, h)
            cover_img = cover_img.crop(((w - cs) // 2, (h - cs) // 2, (w + cs) // 2, (h + cs) // 2))
            cover_img = cover_img.resize((cover_size, cover_size), Image.LANCZOS)
            # White border
            border = 6
            draw.rectangle(
                [cover_x - border, cover_y - border,
                 cover_x + cover_size + border, cover_y + cover_size + border],
                fill="white",
            )
            header_img.paste(cover_img, (cover_x, cover_y), cover_img)
        except Exception:
            pass

    # Right side content — centered within the right half of the screen
    right_half_start = cover_x + cover_size + 20
    right_half_center = right_half_start + (WIDTH - right_half_start) // 2
    right_max_w = WIDTH - right_half_start - margin

    # "ALBUM REVIEW" header
    font_header = ImageFont.truetype(FONT_IMPACT, 52)
    header_text = "ALBUM REVIEW"
    hb = draw.textbbox((0, 0), header_text, font=font_header, stroke_width=4)
    hw = hb[2] - hb[0]
    draw.text((right_half_center - hw // 2, top_padding), header_text, font=font_header,
              fill="#FFD700", stroke_width=4, stroke_fill="black")
    header_h = hb[3] - hb[1]

    # Album title (large, bold)
    font_title = ImageFont.truetype(FONT_IMPACT, 72)
    title_upper = album_name.upper()
    # Wrap if needed
    title_bbox = draw.textbbox((0, 0), title_upper, font=font_title, stroke_width=5)
    title_text_w = title_bbox[2] - title_bbox[0]
    if title_text_w > right_max_w:
        words = title_upper.split()
        lines = []
        current = words[0]
        for word in words[1:]:
            test = current + " " + word
            tb = font_title.getbbox(test)
            if tb[2] - tb[0] <= right_max_w:
                current = test
            else:
                lines.append(current)
                current = word
        lines.append(current)
        title_upper = "\n".join(lines)

    title_y = top_padding + header_h + 14
    if "\n" in title_upper:
        tb = draw.multiline_textbbox((0, 0), title_upper, font=font_title, stroke_width=5)
        title_tw = tb[2] - tb[0]
        title_x = right_half_center - title_tw // 2
        draw.multiline_text((title_x, title_y), title_upper, font=font_title, fill="white",
                            stroke_width=5, stroke_fill="black", align="center")
    else:
        tb = draw.textbbox((0, 0), title_upper, font=font_title, stroke_width=5)
        title_tw = tb[2] - tb[0]
        title_x = right_half_center - title_tw // 2
        draw.text((title_x, title_y), title_upper, font=font_title, fill="white",
                  stroke_width=5, stroke_fill="black")
    title_h = tb[3] - tb[1]

    # Artist name
    font_artist = ImageFont.truetype(FONT_DISPLAY, 48)
    ab = draw.textbbox((0, 0), artist_name, font=font_artist, stroke_width=3)
    artist_w = ab[2] - ab[0]
    artist_x = right_half_center - artist_w // 2
    artist_y = title_y + title_h + 12
    draw.text((artist_x, artist_y), artist_name, font=font_artist, fill="#CCCCCC",
              stroke_width=3, stroke_fill="black")
    artist_h = ab[3] - ab[1]

    # Average rating (large)
    font_avg = ImageFont.truetype(FONT_IMPACT, 88)
    avg_display = f"{avg_rating:.1f}" if avg_rating != int(avg_rating) else f"{int(avg_rating)}"
    avg_text = f"{avg_display}/10"
    avg_bbox = draw.textbbox((0, 0), avg_text, font=font_avg, stroke_width=5)
    avg_w = avg_bbox[2] - avg_bbox[0]
    avg_x = right_half_center - avg_w // 2
    avg_y = artist_y + artist_h + 20
    draw.text((avg_x, avg_y), avg_text, font=font_avg, fill=rating_color(avg_rating),
              stroke_width=5, stroke_fill="black")
    avg_h = avg_bbox[3] - avg_bbox[1]

    # Color legend / key — small colored squares with labels, centered in right half
    font_legend = ImageFont.truetype(FONT_IMPACT, 28)
    legend_y = avg_y + avg_h + 16
    sq_size = 24
    sample_bbox = draw.textbbox((0, 0), "PERFECT", font=font_legend, stroke_width=2)
    legend_block_w = sq_size + 10 + (sample_bbox[2] - sample_bbox[0])
    legend_start_x = right_half_center - legend_block_w // 2
    for _, label, color in RATING_LEGEND:
        draw.rectangle(
            [legend_start_x, legend_y + 3, legend_start_x + sq_size, legend_y + 3 + sq_size],
            fill=color, outline="black", width=1,
        )
        draw.text((legend_start_x + sq_size + 10, legend_y), label.upper(), font=font_legend,
                  fill="white", stroke_width=2, stroke_fill="black")
        legend_y += 32

    header_clip = ImageClip(np.array(header_img), transparent=True).with_duration(duration)
    layers.append(header_clip)

    # --- Song rankings with roll-in animation ---
    # Song list starts right under the album cover
    song_list_start_y = top_padding + cover_size + 25
    num_songs = len(display_songs)

    # Calculate font size to fit all songs — use as much space as possible
    available_height = HEIGHT - song_list_start_y - 50
    max_spacing = available_height / num_songs
    if max_spacing >= 72:
        song_fontsize = 56
        song_spacing = 68
    elif max_spacing >= 62:
        song_fontsize = 48
        song_spacing = 60
    elif max_spacing >= 52:
        song_fontsize = 42
        song_spacing = 52
    elif max_spacing >= 44:
        song_fontsize = 36
        song_spacing = 44
    else:
        song_fontsize = 30
        song_spacing = 36
    font_song = ImageFont.truetype(FONT_IMPACT, song_fontsize)

    # Roll-in animation parameters
    roll_distance = 400
    roll_duration = 0.4
    stagger_delay = 0.08  # delay between each line starting

    for i, song in enumerate(display_songs):
        r = song["rating"]
        color = rating_color(r)

        # Render the song line (rank + name only, color-coded by rating)
        line_text = f"{song['rank']}. {song['name']}"

        line_img = Image.new("RGBA", (WIDTH - 2 * margin, song_spacing), (0, 0, 0, 0))
        line_draw = ImageDraw.Draw(line_img)
        line_draw.text((0, 0), line_text, font=font_song, fill=color,
                       stroke_width=2, stroke_fill="black")

        final_y = song_list_start_y + i * song_spacing
        line_clip = ImageClip(np.array(line_img), transparent=True).with_duration(duration)

        # Each line rolls in with a staggered delay
        delay = i * stagger_delay
        def make_pos(final_x, final_y, delay):
            def pos(t):
                t_adj = t - delay
                if t_adj < 0:
                    return (final_x, final_y + roll_distance)
                progress = min(1.0, t_adj / roll_duration)
                ease = _ease_out_cubic(progress)
                y_offset = roll_distance * (1.0 - ease)
                return (final_x, int(final_y + y_offset))
            return pos

        line_clip = line_clip.with_position(make_pos(margin, final_y, delay))
        layers.append(line_clip)

    return CompositeVideoClip(layers, size=(WIDTH, HEIGHT)).with_duration(duration)


def build_segment(segment, audio_dir, broll_dir, broll_manifest, audio_manifest, broll_assignment=None, fade_duration=0.4):
    """Build a single segment (video + audio + text overlays)."""
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

    # --- Text Overlays ---
    text_layers = []

    countdown_text = f"#{countdown}"
    text_layers.extend(make_text_clip(
        countdown_text, fontsize=120, duration=duration,
        position=(40, 60),
        font_override=FONT_IMPACT,
    ))

    rating_display = int(rating) if rating == int(rating) else rating
    rating_text = f"{rating_display}/10"
    text_layers.extend(make_text_clip(
        rating_text, fontsize=80, duration=duration,
        position=(WIDTH - 250, 80),
        color=rating_color(rating),
        font_override=FONT_IMPACT,
    ))

    name_fontsize = 96 if len(song_name) <= 20 else 72 if len(song_name) <= 30 else 56
    text_layers.extend(make_text_clip(
        song_name, fontsize=name_fontsize, duration=duration,
        position=("center", HEIGHT // 2 - 40),
        font_override=FONT_DISPLAY,
    ))

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
