"""
Generate the static Artist Stats POST image (Instagram portrait, 1080x1350).

Layout:
    [ TITLE bar — "<ARTIST> - <STAT TITLE>"      ]
    [ Grid of album covers, 4 columns:           ]
    [   each cell:                               ]
    [     - album cover                          ]
    [     - stat value overlaid on cover (top)   ]
    [     - song/track label below cover         ]

Usage:
    python -m tools.artist_stats.generate_stats_post "Kanye West" lowest_streamed_per_album
    python -m tools.artist_stats.generate_stats_post "Kanye West" lowest_streamed_per_album --output out.png
"""

import argparse
import io
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))  # parent of albumsdailyy for package imports

from albumsdailyy.tools.artist_stats import stats_registry
from albumsdailyy.tools.shared.video_utils import (
    FONT_IMPACT, FONT_DISPLAY, FONT_BOLD, fit_text_image,
)

# Instagram portrait: 1080 x 1350
POST_W, POST_H = 1080, 1350

# Reel uses the same layout but at 1080x1920 (renders into a 1350-tall canvas vertically centered)
REEL_W, REEL_H = 1080, 1920

# Layout constants
GRID_COLS = 4
HEADER_HEIGHT = 180
SIDE_MARGIN = 28
CELL_PADDING = 14
LABEL_LINE_HEIGHT = 36

# Cover image cache
_COVER_CACHE_DIR = os.path.join(PROJECT_ROOT, ".tmp", "stats_covers")


def _download_cover(url, album_id):
    """Download an album cover image and cache it."""
    os.makedirs(_COVER_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_COVER_CACHE_DIR, f"{album_id}.jpg")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        return cache_path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        with open(cache_path, "wb") as f:
            f.write(resp.read())
    return cache_path


def _load_cover_image(item, target_size):
    """Load+resize an album cover. Returns a PIL.Image or a placeholder."""
    img = None
    url = item.get("album_image")
    if url:
        try:
            # use a hash of url as cache key
            key = str(abs(hash(url)))
            path = _download_cover(url, key)
            img = Image.open(path).convert("RGB")
        except Exception as e:
            print(f"  [cover] failed for {item.get('album_name')}: {e}", flush=True)
    if img is None:
        img = Image.new("RGB", (target_size, target_size), (40, 40, 40))
    img = img.resize((target_size, target_size), Image.LANCZOS)
    return img


def _draw_value_pill(canvas, x, y, w, h, value_text, font):
    """Draw the stat value as a pill overlay on top-left of an album cover."""
    # Semi-transparent black pill background
    pill = Image.new("RGBA", (canvas.size[0], canvas.size[1]), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(pill)
    bbox = pdraw.textbbox((0, 0), value_text, font=font, stroke_width=2)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x, pad_y = 14, 6
    pill_w = text_w + 2 * pad_x
    pill_h = text_h + 2 * pad_y
    px, py = x + 8, y + 8

    pdraw.rounded_rectangle(
        [px, py, px + pill_w, py + pill_h],
        radius=pill_h // 2,
        fill=(0, 0, 0, 200),
    )
    pdraw.text(
        (px + pad_x - bbox[0], py + pad_y - bbox[1]),
        value_text, font=font, fill="white",
        stroke_width=2, stroke_fill="black",
    )
    canvas.paste(pill, (0, 0), pill)


def _draw_title_bar(canvas, draw, artist_name, stat_title_subline):
    """Draw the top title: <ARTIST> - <subline> in a single colorful row."""
    # First line: artist (large, accent color)
    accent = "#E91E63"  # Instagram-pink-ish, matches example
    fa = ImageFont.truetype(FONT_IMPACT, 64)
    fs = ImageFont.truetype(FONT_IMPACT, 36)

    # Two-line layout: top centered "<artist>", second centered "<sub>"
    w_artist = draw.textlength(artist_name, font=fa)
    w_sub = draw.textlength(stat_title_subline, font=fs)

    # Auto-shrink artist if too wide
    while w_artist > POST_W - 2 * SIDE_MARGIN and fa.size > 36:
        fa = ImageFont.truetype(FONT_IMPACT, fa.size - 2)
        w_artist = draw.textlength(artist_name, font=fa)
    while w_sub > POST_W - 2 * SIDE_MARGIN and fs.size > 22:
        fs = ImageFont.truetype(FONT_IMPACT, fs.size - 2)
        w_sub = draw.textlength(stat_title_subline, font=fs)

    y_artist = 30
    y_sub = y_artist + fa.size + 12
    draw.text(
        ((POST_W - w_artist) / 2, y_artist),
        artist_name, font=fa, fill=accent,
        stroke_width=3, stroke_fill="black",
    )
    draw.text(
        ((POST_W - w_sub) / 2, y_sub),
        stat_title_subline, font=fs, fill="white",
        stroke_width=3, stroke_fill="black",
    )


def _split_title(full_title, artist_name):
    """Split 'Kanye West - Lowest Streamed Song on Each Album' -> ('Kanye West', 'Lowest Streamed Song on Each Album')."""
    if full_title.startswith(artist_name + " - "):
        return artist_name, full_title[len(artist_name) + 3:]
    return artist_name, full_title


def render_grid(stat_data, artist_name, canvas_w=POST_W, canvas_h=POST_H, bg=(18, 18, 18)):
    """Render the stats grid onto a single canvas image. Returns PIL.Image.

    `bg=None` renders onto a transparent RGBA canvas — used by the reel compositor
    so the broll shows through the grid's empty space.
    """
    items = stat_data["items"]
    if not items:
        raise ValueError("Stat has no items to render")

    artist, sub = _split_title(stat_data["title"], artist_name)

    if bg is None:
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGB", (canvas_w, canvas_h), bg)
    draw = ImageDraw.Draw(canvas)
    _draw_title_bar(canvas, draw, artist, sub)

    n = len(items)
    cols = GRID_COLS
    rows = (n + cols - 1) // cols

    grid_top = HEADER_HEIGHT
    grid_h_avail = canvas_h - grid_top - SIDE_MARGIN
    grid_w_avail = canvas_w - 2 * SIDE_MARGIN

    cell_w = grid_w_avail / cols
    cell_h = grid_h_avail / rows

    # Cover takes most of the cell, label takes ~LABEL_LINE_HEIGHT*2 below
    cover_size = int(min(cell_w - 2 * CELL_PADDING, cell_h - LABEL_LINE_HEIGHT * 2 - CELL_PADDING))
    cover_size = max(60, cover_size)

    value_font = ImageFont.truetype(FONT_IMPACT, max(22, cover_size // 7))
    label_font = ImageFont.truetype(FONT_BOLD, max(16, cover_size // 9))

    for idx, item in enumerate(items):
        col = idx % cols
        row = idx // cols

        # Center the partial last row
        in_row = min(cols, n - row * cols)
        row_start_x = SIDE_MARGIN + (cols - in_row) * cell_w / 2
        cx = row_start_x + col * cell_w
        cy = grid_top + row * cell_h

        # Cover
        cover_x = int(cx + (cell_w - cover_size) / 2)
        cover_y = int(cy + CELL_PADDING)
        cover_img = _load_cover_image(item, cover_size)
        canvas.paste(cover_img, (cover_x, cover_y))
        # Value pill on top of cover
        _draw_value_pill(canvas, cover_x, cover_y, cover_size, cover_size,
                         item["value_text"], value_font)

        # Label below cover (auto-fit, max 2 lines)
        label_y = cover_y + cover_size + 8
        label_max_w = int(cell_w - 8)
        label_max_h = int(cell_h - (label_y - cy) - 4)
        label_img, lw, lh, _ = fit_text_image(
            item["label_text"], FONT_BOLD, label_max_w, label_max_h, label_font.size,
            fill="white", stroke_width=3, stroke_fill="black", min_size=14, align="center",
        )
        lx = int(cx + (cell_w - lw) / 2)
        canvas.paste(label_img, (lx, label_y), label_img)

    return canvas


def main():
    p = argparse.ArgumentParser(description="Generate an artist stats post image (1080x1350)")
    p.add_argument("artist", help="Artist name")
    p.add_argument("stat_type", choices=list(stats_registry.STAT_TYPES))
    p.add_argument("--output", "-o", help="Output PNG path")
    p.add_argument("--reel-canvas", action="store_true",
                   help="Render onto 1080x1920 (centered) for use as reel base")
    p.add_argument("--refresh", action="store_true", help="Force refresh of kworb cache")
    args = p.parse_args()

    data = stats_registry.build_artist_data(args.artist, force_refresh=args.refresh)
    stat = stats_registry.build_stat(data, args.stat_type)
    print(f"\n[render] {stat['title']} — {len(stat['items'])} items", flush=True)

    if args.reel_canvas:
        # Render the post layout, then paste centered onto a 1920-tall canvas
        post = render_grid(stat, data["name"])
        canvas = Image.new("RGB", (REEL_W, REEL_H), (10, 10, 10))
        canvas.paste(post, (0, (REEL_H - POST_H) // 2))
        out = canvas
    else:
        out = render_grid(stat, data["name"])

    output = args.output or os.path.join(
        PROJECT_ROOT, "outputs", "stats",
        f"{data['name'].replace(' ', '_')}_{args.stat_type}.png",
    )
    os.makedirs(os.path.dirname(output), exist_ok=True)
    out.save(output, "PNG", optimize=True)
    print(f"[render] saved -> {output} ({out.size[0]}x{out.size[1]})", flush=True)


if __name__ == "__main__":
    main()
