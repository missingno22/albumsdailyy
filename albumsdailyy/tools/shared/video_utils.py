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
import re
import urllib.request
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


def _google_font_download(family_name, dest_dir):
    """Download a Google Font as TTF via the google/fonts GitHub mirror. Returns path or None."""
    try:
        safe = re.sub(r"[^A-Za-z0-9]", "", family_name)
        slug = re.sub(r"[^a-z0-9]", "", family_name.lower())
        family_file = re.sub(r"\s+", "", family_name.strip().title())
        dest = os.path.join(dest_dir, f"{safe}.ttf")
        os.makedirs(dest_dir, exist_ok=True)
        # Try each license directory used in google/fonts
        for license_dir in ("ofl", "apache", "ufl"):
            url = (f"https://raw.githubusercontent.com/google/fonts/main/"
                   f"{license_dir}/{slug}/{family_file}-Regular.ttf")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "albumsdailyy/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        continue
                    data = resp.read()
                if len(data) < 1000:
                    continue
                with open(dest, "wb") as f:
                    f.write(data)
                return dest
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue
        # Variable-font fallback (many newer families only ship VF)
        for license_dir in ("ofl", "apache", "ufl"):
            url = (f"https://raw.githubusercontent.com/google/fonts/main/"
                   f"{license_dir}/{slug}/{family_file}[wght].ttf")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "albumsdailyy/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status != 200:
                        continue
                    data = resp.read()
                if len(data) < 1000:
                    continue
                with open(dest, "wb") as f:
                    f.write(data)
                return dest
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue
        print(f"  Google Fonts: '{family_name}' not found in google/fonts mirror")
        return None
    except Exception as e:
        print(f"  Google Fonts download failed for '{family_name}': {e}")
        return None


_SYSTEM_FONT_DIRS = [
    "C:/Windows/Fonts",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts",
]

# Style keywords that shouldn't be part of the family match
_STYLE_WORDS = {
    "italic", "italics", "italicized", "oblique",
    "bold", "regular", "light", "medium", "heavy", "black",
    "thin", "semibold", "extrabold", "condensed",
}

# Common family -> typical Windows filename prefix
_WIN_FAMILY_ALIASES = {
    "arial": "arial", "arial italic": "ariali", "arial bold": "arialbd",
    "arial black": "ariblk", "arial bold italic": "arialbi",
    "times new roman": "times", "times new roman italic": "timesi",
    "times new roman bold": "timesbd",
    "courier new": "cour", "courier new italic": "couri", "courier new bold": "courbd",
    "verdana": "verdana", "tahoma": "tahoma", "georgia": "georgia",
    "comic sans ms": "comic", "impact": "impact", "trebuchet ms": "trebuc",
    "calibri": "calibri", "cambria": "cambria", "consolas": "consola",
    "bahnschrift": "bahnschrift", "segoe ui": "segoeui",
}


def _find_system_font(name):
    """Look up a font by family/filename across common system font directories.
    Returns a path or None. Handles style hints like '(Italic)', 'Italicized', 'Bold'.
    """
    raw = name.strip()
    # Split style hints out of the family string: "Arial (Italicized)" -> family="Arial", style="italic"
    cleaned = re.sub(r"[\(\)\[\]_,-]", " ", raw).strip()
    tokens = cleaned.split()
    family_tokens = []
    style_tokens = []
    for tok in tokens:
        t = tok.lower().rstrip("s")  # "italics" -> "italic"
        if t in _STYLE_WORDS or tok.lower() in _STYLE_WORDS:
            style_tokens.append(t)
        else:
            family_tokens.append(tok)
    family = " ".join(family_tokens).strip()
    # Normalize "italicized" -> "italic"
    style_tokens = ["italic" if t == "italicized" else t for t in style_tokens]
    style_key = " ".join(style_tokens).strip()

    # Windows alias table (fast path)
    alias_key = f"{family} {style_key}".strip().lower()
    win_dir = "C:/Windows/Fonts"
    if alias_key in _WIN_FAMILY_ALIASES:
        p = os.path.join(win_dir, _WIN_FAMILY_ALIASES[alias_key] + ".ttf")
        if os.path.exists(p):
            return p
    if family.lower() in _WIN_FAMILY_ALIASES and not style_key:
        p = os.path.join(win_dir, _WIN_FAMILY_ALIASES[family.lower()] + ".ttf")
        if os.path.exists(p):
            return p

    # Generic search: look for any .ttf/.otf whose filename contains the family tokens
    family_norm = re.sub(r"[^a-z0-9]", "", family.lower())
    if not family_norm:
        return None
    exts = (".ttf", ".otf")
    for d in _SYSTEM_FONT_DIRS:
        if not d or not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fname in files:
                if not fname.lower().endswith(exts):
                    continue
                fnorm = re.sub(r"[^a-z0-9]", "", os.path.splitext(fname)[0].lower())
                if family_norm in fnorm:
                    # Style match: if style requested, require it in filename too
                    if style_key:
                        style_norm = re.sub(r"[^a-z0-9]", "", style_key)
                        if style_norm not in fnorm:
                            continue
                    return os.path.join(root, fname)
    return None


def resolve_album_font(font_name, fallback=None):
    """Resolve an album-specific font name to a .ttf/.otf path.
    Order: exact file in fonts/, normalized family in fonts/, system fonts, Google Fonts download, fallback.
    """
    fb = fallback or FONT_IMPACT
    if not font_name:
        return fb
    name = str(font_name).strip()
    if not name:
        return fb
    # As-provided filename
    cand = name if name.lower().endswith((".ttf", ".otf")) else f"{name}.ttf"
    p = cand if os.path.isabs(cand) else os.path.join(_FONTS_DIR, cand)
    if os.path.exists(p):
        return p
    # Normalized family name (spaces stripped) inside repo fonts/
    safe = re.sub(r"[^A-Za-z0-9]", "", name)
    p = os.path.join(_FONTS_DIR, f"{safe}.ttf")
    if os.path.exists(p):
        return p
    # System fonts
    sys_p = _find_system_font(name)
    if sys_p:
        print(f"  Resolved album font '{name}' from system -> {sys_p}")
        return sys_p
    # Google Fonts
    downloaded = _google_font_download(name, _FONTS_DIR)
    if downloaded and os.path.exists(downloaded):
        print(f"  Resolved album font '{name}' from Google Fonts -> {downloaded}")
        return downloaded
    print(f"  Could not resolve album font '{name}', using fallback")
    return fb


def _word_width(font, word):
    b = font.getbbox(word)
    return b[2] - b[0]


def _wrap_to_fit(text, font, max_width):
    """Word-wrap text; if any single word is wider than max_width, char-wrap it."""
    words = text.split()
    if not words:
        return text
    lines = []
    current = ""
    for word in words:
        if _word_width(font, word) > max_width:
            if current:
                lines.append(current)
                current = ""
            chunk = ""
            for ch in word:
                test = chunk + ch
                if _word_width(font, test) <= max_width:
                    chunk = test
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = ch
            current = chunk
            continue
        test_line = (current + " " + word).strip() if current else word
        if _word_width(font, test_line) <= max_width:
            current = test_line
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def fit_text_image(text, font_path, max_width, max_height, start_size,
                   fill="white", stroke_width=4, stroke_fill="black",
                   min_size=20, align="center"):
    """Render text to a transparent RGBA image that fits within max_width x max_height.
    Shrinks font size and wraps (word-wrap, char-wrap fallback) until it fits.
    Returns (PIL.Image, img_w, img_h, final_fontsize).
    """
    size = max(min_size, int(start_size))
    wrapped = text
    bbox = (0, 0, 0, 0)
    font = ImageFont.truetype(font_path, size)
    tmp = Image.new("RGBA", (1, 1))
    td = ImageDraw.Draw(tmp)

    while True:
        font = ImageFont.truetype(font_path, size)
        wrapped = _wrap_to_fit(text, font, max_width)
        if "\n" in wrapped:
            bbox = td.multiline_textbbox((0, 0), wrapped, font=font,
                                         stroke_width=stroke_width, align=align)
        else:
            bbox = td.textbbox((0, 0), wrapped, font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if (w <= max_width and h <= max_height) or size <= min_size:
            break
        size -= 2

    img_w = int(max(1, bbox[2] - bbox[0] + 16))
    img_h = int(max(1, bbox[3] - bbox[1] + 16))
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if "\n" in wrapped:
        draw.multiline_text((8 - bbox[0], 8 - bbox[1]), wrapped, font=font, fill=fill,
                            stroke_width=stroke_width, stroke_fill=stroke_fill, align=align)
    else:
        draw.text((8 - bbox[0], 8 - bbox[1]), wrapped, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
    return img, img_w, img_h, size


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


def make_text_clip(text, fontsize, duration, position, color="white", bold=True,
                   font_override=None, max_width=None, max_height=None, min_size=24,
                   stroke_width=4):
    """Create a text clip using PIL with robust auto-fit (shrinks + wraps to fit box)."""
    font_path = font_override or (FONT_BOLD if bold else FONT_REGULAR)
    mw = max_width if max_width is not None else (WIDTH - 80)
    mh = max_height if max_height is not None else (HEIGHT - 160)

    img, text_w, text_h, _ = fit_text_image(
        text, font_path, mw, mh, fontsize,
        fill=color, stroke_width=stroke_width, stroke_fill="black",
        min_size=min_size,
    )

    clip = ImageClip(np.array(img), transparent=True).with_duration(duration)

    px, py = position
    if px == "center":
        px = (WIDTH - text_w) // 2
    if py == "center":
        py = (HEIGHT - text_h) // 2

    # Safety: keep fully on-canvas
    px = max(0, min(px, WIDTH - text_w))
    py = max(0, min(py, HEIGHT - text_h))

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


def build_end_card(album_data, cover_path, broll_manifest, broll_dir, duration=6.0, album_font=None):
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

    headline_font = album_font or FONT_IMPACT

    # "ALBUM REVIEW" header — fit
    hdr_img, hdr_w, hdr_h, _ = fit_text_image(
        "ALBUM REVIEW", headline_font, right_max_w, 70, 52,
        fill="#FFD700", stroke_width=4, stroke_fill="black", min_size=24,
    )
    header_img.paste(hdr_img, (right_half_center - hdr_w // 2, top_padding), hdr_img)

    # Album title — fit to right column width and a height cap (leaves room for artist/rating/legend)
    title_upper = album_name.upper()
    title_max_h = 200
    t_img, t_w, t_h, _ = fit_text_image(
        title_upper, headline_font, right_max_w, title_max_h, 72,
        fill="white", stroke_width=5, stroke_fill="black", min_size=24,
    )
    title_y = top_padding + hdr_h + 14
    header_img.paste(t_img, (right_half_center - t_w // 2, title_y), t_img)

    # Artist name — fit
    a_img, a_w, a_h, _ = fit_text_image(
        artist_name, FONT_DISPLAY, right_max_w, 70, 48,
        fill="#CCCCCC", stroke_width=3, stroke_fill="black", min_size=22,
    )
    artist_y = title_y + t_h + 12
    header_img.paste(a_img, (right_half_center - a_w // 2, artist_y), a_img)

    # Average rating
    avg_display = f"{avg_rating:.1f}" if avg_rating != int(avg_rating) else f"{int(avg_rating)}"
    avg_text = f"{avg_display}/10"
    av_img, av_w, av_h, _ = fit_text_image(
        avg_text, headline_font, right_max_w, 120, 88,
        fill=rating_color(avg_rating), stroke_width=5, stroke_fill="black", min_size=36,
    )
    avg_y = artist_y + a_h + 20
    header_img.paste(av_img, (right_half_center - av_w // 2, avg_y), av_img)

    # Shadow locals used below
    avg_h = av_h

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

    # Width safety: shrink until all song lines fit horizontally
    list_font_path = album_font or FONT_IMPACT
    max_line_w = WIDTH - 2 * margin
    while song_fontsize >= 18:
        probe = ImageFont.truetype(list_font_path, song_fontsize)
        widest = 0
        for s in display_songs:
            line_text = f"{s['rank']}. {s['name']}"
            b = probe.getbbox(line_text)
            widest = max(widest, b[2] - b[0])
        if widest <= max_line_w:
            break
        song_fontsize -= 2
        song_spacing = max(int(song_fontsize * 1.15), 22)
    font_song = ImageFont.truetype(list_font_path, song_fontsize)

    # Roll-in animation parameters — cap so no clip starts below the canvas
    last_final_y = song_list_start_y + (num_songs - 1) * song_spacing
    roll_distance = min(400, max(20, HEIGHT - last_final_y - song_spacing))
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


def build_segment(segment, audio_dir, broll_dir, broll_manifest, audio_manifest,
                  broll_assignment=None, fade_duration=0.4, album_font=None):
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

    headline_font = album_font or FONT_IMPACT

    # Top strip: countdown (left) and rating (right) — reserve a band ~220px tall
    countdown_text = f"#{countdown}"
    text_layers.extend(make_text_clip(
        countdown_text, fontsize=120, duration=duration,
        position=(40, 60),
        font_override=headline_font,
        max_width=WIDTH // 2 - 80, max_height=180, min_size=60,
    ))

    rating_display = int(rating) if rating == int(rating) else rating
    rating_text = f"{rating_display}/10"
    # Render first to measure, then right-anchor
    rating_img, rw, rh, _ = fit_text_image(
        rating_text, headline_font, WIDTH // 2 - 80, 160, 80,
        fill=rating_color(rating), stroke_width=4, stroke_fill="black", min_size=40,
    )
    rating_clip = (ImageClip(np.array(rating_img), transparent=True)
                   .with_duration(duration)
                   .with_position((WIDTH - rw - 40, 80)))
    text_layers.append(rating_clip)

    # Song name — auto-fit within full width and a vertical band around center
    name_max_h = int(HEIGHT * 0.35)
    name_img, nw, nh, _ = fit_text_image(
        song_name, album_font or FONT_DISPLAY, WIDTH - 80, name_max_h, 96,
        fill="white", stroke_width=4, stroke_fill="black", min_size=28,
    )
    name_clip = (ImageClip(np.array(name_img), transparent=True)
                 .with_duration(duration)
                 .with_position(((WIDTH - nw) // 2, (HEIGHT - nh) // 2 - 40)))
    text_layers.append(name_clip)

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
