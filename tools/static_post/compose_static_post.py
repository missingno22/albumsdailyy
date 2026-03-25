"""
Compose a multi-image Instagram carousel post from album ranking data.

Outputs 1080x1350 (4:5) PNG images:
  - Slide 1: Title card with hook text, large album cover, album/artist info
  - Slide 2: Album cover + overall score + "SWIPE FOR RANKINGS ----->"
  - Slides 3-N: 10 songs per slide (excluding #1), worst to best
  - Final slide: #1 song grand reveal

Usage: python tools/static_post/compose_static_post.py [--album .tmp/album_data.json]
                                                         [--broll-dir .tmp/broll]
                                                         [--output-dir .tmp/output]
"""

import json
import math
import os
import sys
import argparse
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Add tools directory to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.video_utils import (
    FONT_BOLD, FONT_REGULAR, FONT_IMPACT, FONT_DISPLAY,
    RATING_LEGEND,
    rating_color,
    wrap_text,
)

# === Constants ===
POST_WIDTH = 1080
POST_HEIGHT = 1350  # 4:5 Instagram portrait


def build_background(cover_path, width=POST_WIDTH, height=POST_HEIGHT):
    """Build a blurred album cover background with dark overlay. Falls back to solid dark."""
    if cover_path and os.path.exists(cover_path):
        try:
            bg = Image.open(cover_path).convert("RGB")
            # Scale to fill the frame
            bg_ratio = width / height
            img_ratio = bg.width / bg.height
            if img_ratio > bg_ratio:
                new_h = height
                new_w = int(height * img_ratio)
            else:
                new_w = width
                new_h = int(width / img_ratio)
            bg = bg.resize((new_w, new_h), Image.LANCZOS)
            # Center crop
            left = (new_w - width) // 2
            top = (new_h - height) // 2
            bg = bg.crop((left, top, left + width, top + height))
            # Blur
            bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
            # Dark overlay at 70% opacity
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 178))
            bg = bg.convert("RGBA")
            bg = Image.alpha_composite(bg, overlay)
            return bg.convert("RGB")
        except Exception as e:
            print(f"  Warning: Background from cover failed: {e}")

    return Image.new("RGB", (width, height), (15, 15, 15))


def load_cover(cover_path, size):
    """Load and square-crop the album cover to the given size."""
    if not cover_path or not os.path.exists(cover_path):
        return None
    try:
        img = Image.open(cover_path).convert("RGBA")
        w, h = img.size
        cs = min(w, h)
        img = img.crop(((w - cs) // 2, (h - cs) // 2, (w + cs) // 2, (h + cs) // 2))
        img = img.resize((size, size), Image.LANCZOS)
        return img
    except Exception:
        return None


def build_title_slide(album_data, cover_path):
    """Build slide 1: title card with hook, large album cover, album/artist info."""
    album_name = album_data["album"]
    artist_name = album_data["artist"]
    album_number = album_data.get("album_number")

    print("  Building title slide...")

    bg = build_background(cover_path)
    card = Image.new("RGBA", (POST_WIDTH, POST_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    # Album counter top-left (e.g. "1/1000")
    if album_number is not None:
        font_counter = ImageFont.truetype(FONT_IMPACT, 48)
        counter_text = f"{album_number}/1000"
        draw.text((40, 40), counter_text, font=font_counter, fill="white",
                  stroke_width=3, stroke_fill="black")

    # Hook text
    font_hook = ImageFont.truetype(FONT_IMPACT, 60)
    hook_text = "EVERY SONG RATED"
    hook_bbox = draw.textbbox((0, 0), hook_text, font=font_hook, stroke_width=4)
    hook_w = hook_bbox[2] - hook_bbox[0]
    hook_x = (POST_WIDTH - hook_w) // 2
    draw.text((hook_x, 120), hook_text, font=font_hook, fill="#FFD700",
              stroke_width=4, stroke_fill="black")

    # Album cover — large, centered
    cover_size = 700
    border_width = 8
    cover_y = 220
    cover_x = (POST_WIDTH - cover_size) // 2

    cover_img = load_cover(cover_path, cover_size)
    if cover_img:
        border_rect = [
            cover_x - border_width, cover_y - border_width,
            cover_x + cover_size + border_width, cover_y + cover_size + border_width,
        ]
        draw.rectangle(border_rect, fill="white", outline="white")
        card.paste(cover_img, (cover_x, cover_y), cover_img)

    # Album name
    font_album = ImageFont.truetype(FONT_IMPACT, 72)
    album_upper = album_name.upper()
    wrapped_album = wrap_text(album_upper, font_album, POST_WIDTH - 100)
    if "\n" in wrapped_album:
        album_bbox = draw.multiline_textbbox((0, 0), wrapped_album, font=font_album, stroke_width=4)
    else:
        album_bbox = draw.textbbox((0, 0), wrapped_album, font=font_album, stroke_width=4)
    album_w = album_bbox[2] - album_bbox[0]
    album_x = (POST_WIDTH - album_w) // 2
    album_y = cover_y + cover_size + 30
    draw_method = draw.multiline_text if "\n" in wrapped_album else draw.text
    draw_method((album_x, album_y), wrapped_album, font=font_album, fill="white",
                stroke_width=4, stroke_fill="black", align="center")

    # Artist name
    album_text_h = album_bbox[3] - album_bbox[1]
    font_artist = ImageFont.truetype(FONT_DISPLAY, 44)
    artist_bbox = draw.textbbox((0, 0), artist_name, font=font_artist, stroke_width=3)
    artist_w = artist_bbox[2] - artist_bbox[0]
    artist_x = (POST_WIDTH - artist_w) // 2
    artist_y = album_y + album_text_h + 15
    draw.text((artist_x, artist_y), artist_name, font=font_artist, fill="#CCCCCC",
              stroke_width=3, stroke_fill="black")

    # Composite onto background
    bg = bg.convert("RGBA")
    result = Image.alpha_composite(bg, card)
    return result.convert("RGB")


def build_swipe_slide(album_data, cover_path):
    """Build slide 2: album cover + overall score + swipe CTA."""
    album_name = album_data["album"]
    songs = album_data["songs"]

    avg_rating = sum(s["rating"] for s in songs) / len(songs)
    avg_rating = round(avg_rating * 2) / 2

    print("  Building swipe slide...")

    bg = build_background(cover_path)
    card = Image.new("RGBA", (POST_WIDTH, POST_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    # Album title at top
    font_title = ImageFont.truetype(FONT_IMPACT, 64)
    title_upper = album_name.upper()
    wrapped_title = wrap_text(title_upper, font_title, POST_WIDTH - 100)
    if "\n" in wrapped_title:
        title_bbox = draw.multiline_textbbox((0, 0), wrapped_title, font=font_title, stroke_width=4)
    else:
        title_bbox = draw.textbbox((0, 0), wrapped_title, font=font_title, stroke_width=4)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (POST_WIDTH - title_w) // 2
    title_draw = draw.multiline_text if "\n" in wrapped_title else draw.text
    title_draw((title_x, 60), wrapped_title, font=font_title, fill="white",
               stroke_width=4, stroke_fill="black", align="center")

    # Album cover — large, centered
    title_h = title_bbox[3] - title_bbox[1]
    cover_size = 600
    border_width = 8
    cover_y = 60 + title_h + 50
    cover_x = (POST_WIDTH - cover_size) // 2

    cover_img = load_cover(cover_path, cover_size)
    if cover_img:
        border_rect = [
            cover_x - border_width, cover_y - border_width,
            cover_x + cover_size + border_width, cover_y + cover_size + border_width,
        ]
        draw.rectangle(border_rect, fill="white", outline="white")
        card.paste(cover_img, (cover_x, cover_y), cover_img)

    # Overall score below cover
    font_score = ImageFont.truetype(FONT_IMPACT, 120)
    avg_display = f"{avg_rating:.1f}" if avg_rating != int(avg_rating) else f"{int(avg_rating)}"
    score_text = f"{avg_display}/10"
    score_bbox = draw.textbbox((0, 0), score_text, font=font_score, stroke_width=5)
    score_w = score_bbox[2] - score_bbox[0]
    score_x = (POST_WIDTH - score_w) // 2
    score_y = cover_y + cover_size + 40
    draw.text((score_x, score_y), score_text, font=font_score, fill=rating_color(avg_rating),
              stroke_width=5, stroke_fill="black")

    # "SWIPE FOR RANKINGS ----->" CTA
    font_cta = ImageFont.truetype(FONT_IMPACT, 52)
    cta_text = "Swipe for Rankings \u2192"
    cta_bbox = draw.textbbox((0, 0), cta_text, font=font_cta, stroke_width=3)
    cta_w = cta_bbox[2] - cta_bbox[0]
    cta_x = (POST_WIDTH - cta_w) // 2
    cta_y = POST_HEIGHT - 120
    draw.text((cta_x, cta_y), cta_text, font=font_cta, fill="#FFD700",
              stroke_width=3, stroke_fill="black")

    # Composite onto background
    bg = bg.convert("RGBA")
    result = Image.alpha_composite(bg, card)
    return result.convert("RGB")


def build_ranking_slide(songs_chunk, cover_path, page_num, total_pages):
    """Build a ranking slide showing up to 10 songs, filling the entire slide."""
    print(f"  Building ranking slide {page_num}/{total_pages}...")

    bg = build_background(cover_path)
    card = Image.new("RGBA", (POST_WIDTH, POST_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    margin = 60
    num_songs = len(songs_chunk)

    # Calculate font size and spacing to fill the slide
    # Available height: roughly 80 to 1280 = 1200px
    top_y = 80
    bottom_y = POST_HEIGHT - 70
    available_height = bottom_y - top_y

    # Scale font and spacing to fill available space
    song_spacing = available_height // num_songs
    # Clamp spacing so text doesn't get absurdly large
    song_spacing = min(song_spacing, 120)
    song_fontsize = min(int(song_spacing * 0.6), 64)

    font_song = ImageFont.truetype(FONT_IMPACT, song_fontsize)

    # Center the block vertically
    total_block_height = song_spacing * num_songs
    start_y = top_y + (available_height - total_block_height) // 2

    for i, song in enumerate(songs_chunk):
        r = song["rating"]
        r_display = int(r) if r == int(r) else r
        color = rating_color(r)
        line = f"{song['rank']}. {song['name']} \u2014 {r_display}/10"

        y = start_y + i * song_spacing

        draw.text((margin, y), line, font=font_song, fill=color,
                  stroke_width=3, stroke_fill="black")

    # Composite onto background
    bg = bg.convert("RGBA")
    result = Image.alpha_composite(bg, card)
    return result.convert("RGB")


def build_reveal_slide(song, cover_path):
    """Build the final slide: #1 song grand reveal."""
    print("  Building #1 reveal slide...")

    bg = build_background(cover_path)
    card = Image.new("RGBA", (POST_WIDTH, POST_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    # "#1" large at top
    font_number = ImageFont.truetype(FONT_IMPACT, 160)
    num_text = "#1"
    num_bbox = draw.textbbox((0, 0), num_text, font=font_number, stroke_width=6)
    num_w = num_bbox[2] - num_bbox[0]
    num_x = (POST_WIDTH - num_w) // 2
    draw.text((num_x, 100), num_text, font=font_number, fill="#FFD700",
              stroke_width=6, stroke_fill="black")

    # Album cover centered
    cover_size = 500
    border_width = 8
    cover_y = 340
    cover_x = (POST_WIDTH - cover_size) // 2

    cover_img = load_cover(cover_path, cover_size)
    if cover_img:
        border_rect = [
            cover_x - border_width, cover_y - border_width,
            cover_x + cover_size + border_width, cover_y + cover_size + border_width,
        ]
        draw.rectangle(border_rect, fill="white", outline="white")
        card.paste(cover_img, (cover_x, cover_y), cover_img)

    # Song name — large, centered below cover
    font_name = ImageFont.truetype(FONT_IMPACT, 72)
    song_name = song["name"].upper()
    wrapped_name = wrap_text(song_name, font_name, POST_WIDTH - 100)
    if "\n" in wrapped_name:
        name_bbox = draw.multiline_textbbox((0, 0), wrapped_name, font=font_name, stroke_width=4)
    else:
        name_bbox = draw.textbbox((0, 0), wrapped_name, font=font_name, stroke_width=4)
    name_w = name_bbox[2] - name_bbox[0]
    name_x = (POST_WIDTH - name_w) // 2
    name_y = cover_y + cover_size + 40
    name_draw = draw.multiline_text if "\n" in wrapped_name else draw.text
    name_draw((name_x, name_y), wrapped_name, font=font_name, fill="white",
              stroke_width=4, stroke_fill="black", align="center")

    # Rating — large, colored
    name_h = name_bbox[3] - name_bbox[1]
    r = song["rating"]
    r_display = int(r) if r == int(r) else r
    font_rating = ImageFont.truetype(FONT_IMPACT, 100)
    rating_str = f"{r_display}/10"
    rating_bbox = draw.textbbox((0, 0), rating_str, font=font_rating, stroke_width=5)
    rating_w = rating_bbox[2] - rating_bbox[0]
    rating_x = (POST_WIDTH - rating_w) // 2
    rating_y = name_y + name_h + 25
    draw.text((rating_x, rating_y), rating_str, font=font_rating, fill=rating_color(r),
              stroke_width=5, stroke_fill="black")

    # Composite onto background
    bg = bg.convert("RGBA")
    result = Image.alpha_composite(bg, card)
    return result.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Compose static Instagram carousel post")
    parser.add_argument("--album", default=".tmp/album_data.json")
    parser.add_argument("--broll-dir", default=".tmp/broll")
    parser.add_argument("--output-dir", default=".tmp/output")
    args = parser.parse_args()

    if not os.path.exists(args.album):
        print(f"Error: Album data not found: {args.album}")
        sys.exit(1)

    with open(args.album, "r", encoding="utf-8") as f:
        album_data = json.load(f)

    # Load cover path from broll manifest
    cover_path = None
    broll_manifest_path = os.path.join(args.broll_dir, "manifest.json")
    if os.path.exists(broll_manifest_path):
        with open(broll_manifest_path, "r", encoding="utf-8") as f:
            broll_manifest = json.load(f)
        if isinstance(broll_manifest, dict):
            cover_path = broll_manifest.get("album_cover")

    if not cover_path or not os.path.exists(cover_path):
        print("Warning: No album cover found. Using solid background.")

    songs = album_data["songs"]
    # Sort worst to best (highest rank number first) to build toward the #1 reveal
    display_songs = sorted(songs, key=lambda s: s["rank"], reverse=True)

    # Separate #1 song from the rest
    number_one = [s for s in display_songs if s["rank"] == 1][0]
    remaining_songs = [s for s in display_songs if s["rank"] != 1]

    # Distribute songs evenly across pages (max 10 per page)
    ranking_pages = math.ceil(len(remaining_songs) / 10)
    # Even distribution: e.g. 13 songs across 2 pages = 7 + 6 instead of 10 + 3
    base_per_page = len(remaining_songs) // ranking_pages
    extra = len(remaining_songs) % ranking_pages
    total_slides = 2 + ranking_pages + 1  # title + swipe + ranking pages + reveal

    print(f"Composing static post: {album_data['album']} by {album_data['artist']}")
    print(f"  {album_data['total_songs']} songs | {total_slides} slides | Format: {POST_WIDTH}x{POST_HEIGHT} (4:5)")

    os.makedirs(args.output_dir, exist_ok=True)
    slide_num = 1

    # Slide 1: Title card
    slide = build_title_slide(album_data, cover_path)
    path = os.path.join(args.output_dir, f"post_slide_{slide_num}.png")
    slide.save(path, "PNG")
    print(f"  Saved: {path} ({os.path.getsize(path) / 1024:.0f} KB)")
    slide_num += 1

    # Slide 2: Swipe slide
    slide = build_swipe_slide(album_data, cover_path)
    path = os.path.join(args.output_dir, f"post_slide_{slide_num}.png")
    slide.save(path, "PNG")
    print(f"  Saved: {path} ({os.path.getsize(path) / 1024:.0f} KB)")
    slide_num += 1

    # Slides 3-N: Ranking pages (evenly distributed, excluding #1)
    offset = 0
    for page in range(ranking_pages):
        # Earlier pages get one extra song if there's a remainder
        page_size = base_per_page + (1 if page < extra else 0)
        chunk = remaining_songs[offset : offset + page_size]
        offset += page_size
        slide = build_ranking_slide(chunk, cover_path, page + 1, ranking_pages)
        path = os.path.join(args.output_dir, f"post_slide_{slide_num}.png")
        slide.save(path, "PNG")
        print(f"  Saved: {path} ({os.path.getsize(path) / 1024:.0f} KB)")
        slide_num += 1

    # Final slide: #1 reveal
    slide = build_reveal_slide(number_one, cover_path)
    path = os.path.join(args.output_dir, f"post_slide_{slide_num}.png")
    slide.save(path, "PNG")
    print(f"  Saved: {path} ({os.path.getsize(path) / 1024:.0f} KB)")

    print(f"\nDone! {total_slides} slides saved to {args.output_dir}/")


if __name__ == "__main__":
    main()
