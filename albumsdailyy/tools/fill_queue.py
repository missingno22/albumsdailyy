"""
Fill the posting queue with the 3-day alternating schedule:

    Day N      19:00  album_review (reel)
    Day N+1    14:00  stats_reel    (reel)   ┐  same artist+stat_type
    Day N+1    20:00  stats_post    (image)  ┘  (the artist of the day-N review)
    Day N+2    19:00  filler        (reel)   ←  popular artist in same genre, TikTok meme
    Day N+3    19:00  album_review (next artist)
    ...

Fallbacks:
  - Stats day with all stat-types used for that artist  → album_review.
  - Filler day with no clip / no available filler artist → album_review.

Usage:
    python -m albumsdailyy.tools.fill_queue
    python -m albumsdailyy.tools.fill_queue --days 14
    python -m albumsdailyy.tools.fill_queue --draft
    python -m albumsdailyy.tools.fill_queue --json   # emits JSON lines for the Flask scheduler
"""

import argparse
import glob
import json as _json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
sys.path.insert(0, os.path.dirname(PROJECT_ROOT))  # parent of albumsdailyy for package imports

from albumsdailyy.tools.parse_markdown import parse_album_markdown
from albumsdailyy.tools.flask_app.models import (
    init_db, has_entry_for_date, has_slot_for_date, is_album_queued_or_posted,
    insert_queue_entry, get_rotation_index, advance_rotation,
    get_pending_count, get_buffer_days,
    list_used_stats_for_artist, record_stat_used,
    get_last_scheduled_entry, get_last_album_review_artist,
    recent_filler_artists,
)
from albumsdailyy.tools.artist_stats import stats_registry
from albumsdailyy.tools.filler.generate_filler_post import build_filler_post


# --- Slot definitions ---
SLOT_ALBUM_REVIEW = "19:00"
SLOT_STATS_REEL = "14:00"
SLOT_STATS_POST = "20:00"
SLOT_FILLER = "19:00"


# ============================== Album reviews ==============================

def get_albums():
    """Get all album markdown files sorted by number prefix."""
    pattern = os.path.join(PROJECT_ROOT, "inputs", "*.md")
    files = glob.glob(pattern)

    def sort_key(path):
        name = os.path.basename(path)
        match = re.match(r"(\d+)", name)
        return int(match.group(1)) if match else 999

    return sorted(files, key=sort_key)


def get_slug(album_path):
    """'inputs/1-CollegeDropout.md' -> '1-CollegeDropout'"""
    return os.path.splitext(os.path.basename(album_path))[0]


def generate_album_caption(album_data):
    """Caption for an album review post."""
    songs = album_data["songs"]
    avg = sum(s["rating"] for s in songs) / len(songs)
    top_song = min(songs, key=lambda s: s["rank"])
    return (
        f"Every song on {album_data['album']} by {album_data['artist']}, "
        f"rated and ranked.\n\n"
        f"Average: {avg:.1f}/10\n"
        f"#1: {top_song['name']} - {top_song['rating']:.0f}/10\n\n"
        f"Do you agree with my rankings?\n\n"
        f"#albumranking #musicreview "
        f"#{album_data['artist'].lower().replace(' ', '')} "
        f"#{album_data['album'].lower().replace(' ', '').replace(':', '')}"
    )


def ensure_endcard(album_path, slug, draft=False):
    """Generate endcard video if it doesn't exist. Returns video path or None."""
    video_path = os.path.join(PROJECT_ROOT, "outputs", "endcards", f"{slug}.mp4")

    if os.path.exists(video_path):
        print(f"  [endcard] Already exists: {video_path}")
        return video_path

    print(f"  [endcard] Generating for {slug}...")
    cmd = [
        sys.executable, "-u",
        os.path.join(PROJECT_ROOT, "tools", "generate_endcard_lite.py"),
        album_path,
        "--output", video_path,
    ]
    if draft:
        cmd.append("--draft")

    process = subprocess.Popen(
        cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in process.stdout:
        print(f"    {line.rstrip()}", flush=True)
    process.wait(timeout=900)

    if process.returncode != 0:
        print(f"  [endcard] ERROR: generation failed for {slug} (exit code {process.returncode})")
        return None
    if os.path.exists(video_path):
        print(f"  [endcard] Done: {video_path}")
        return video_path
    print(f"  [endcard] ERROR: file not created: {video_path}")
    return None


# ============================== Artist stats ==============================

def generate_stats_caption(artist_name, stat_type, item_count, post_format):
    """Caption for a stats post/reel."""
    titles = {
        "lowest_streamed_per_album":  "the LEAST streamed song on each of their albums",
        "highest_streamed_per_album": "the MOST streamed song on each of their albums",
        "longest_song_per_album":     "the LONGEST song on each of their albums",
        "shortest_song_per_album":    "the SHORTEST song on each of their albums",
    }
    body = titles.get(stat_type, stat_type.replace("_", " "))
    tag_artist = re.sub(r"[^a-z0-9]", "", artist_name.lower())
    suffix = "Watch the reel for the full breakdown!" if post_format == "image" else "Like + share if any of these surprised you."
    return (
        f"{artist_name} — {body}.\n\n"
        f"{suffix}\n\n"
        f"#spotifystats #musicstats #albumranking #{tag_artist}"
    )


def _stats_output_paths(artist_name, stat_type):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", artist_name).strip("_")
    reel_dir = os.path.join(PROJECT_ROOT, "outputs", "stats_reels")
    post_dir = os.path.join(PROJECT_ROOT, "outputs", "stats")
    os.makedirs(reel_dir, exist_ok=True)
    os.makedirs(post_dir, exist_ok=True)
    return (
        os.path.join(reel_dir, f"{safe}_{stat_type}.mp4"),
        os.path.join(post_dir, f"{safe}_{stat_type}.png"),
    )


def ensure_stats_assets(artist_name, stat_type, draft=False):
    """Render stats reel + post if missing. Returns (reel_path, post_image_path, item_count) or None."""
    print(f"  [stats] preparing {artist_name} / {stat_type}", flush=True)
    try:
        artist_data = stats_registry.build_artist_data(artist_name)
        stat = stats_registry.build_stat(artist_data, stat_type)
    except Exception as e:
        print(f"  [stats] ERROR building data: {e}", flush=True)
        return None

    if not stat["items"]:
        print(f"  [stats] WARNING: stat had no items, skipping", flush=True)
        return None

    reel_path, post_path = _stats_output_paths(artist_name, stat_type)

    # Render image (post)
    if not os.path.exists(post_path):
        print(f"  [stats] rendering image post -> {post_path}", flush=True)
        from albumsdailyy.tools.artist_stats.generate_stats_post import render_grid
        img = render_grid(stat, artist_data["name"])
        img.save(post_path, "PNG", optimize=True)
    else:
        print(f"  [stats] image cached: {post_path}", flush=True)

    # Render reel
    if not os.path.exists(reel_path):
        print(f"  [stats] rendering reel -> {reel_path}", flush=True)
        from albumsdailyy.tools.artist_stats.generate_stats_reel import build_reel
        try:
            build_reel(artist_data, stat, reel_path, draft=draft)
        except Exception as e:
            print(f"  [stats] ERROR rendering reel: {e}", flush=True)
            return None
    else:
        print(f"  [stats] reel cached: {reel_path}", flush=True)

    return reel_path, post_path, len(stat["items"])


def pick_unused_stat_type(artist_name):
    """Return the first stat_type from STAT_TYPES order that isn't used yet, or None."""
    used = list_used_stats_for_artist(artist_name)
    for stat_type in stats_registry.STAT_TYPES:
        if stat_type not in used:
            return stat_type
    return None


# ============================== Scheduling ==============================

def _next_post_type(last_entry):
    """Decide the next slot type from the last queued entry.

    Returns one of: 'album_review', 'stats_day', 'filler_day'.

    Cycle:  album_review -> stats_day -> filler_day -> album_review -> ...
    """
    if last_entry is None:
        return "album_review"
    pt = last_entry["post_type"]
    if pt == "album_review":
        return "stats_day"
    if pt in ("stats_reel", "stats_post"):
        return "filler_day"
    if pt == "filler":
        return "album_review"
    # Unknown post_type → safe default
    return "album_review"


def _emit_json(json_output, *, title, video_path, caption, scheduled_date, scheduled_time):
    if not json_output:
        return
    print(_json.dumps({
        "title": title,
        "video_path": os.path.abspath(video_path),
        "caption": caption,
        "scheduled_datetime": f"{scheduled_date}T{scheduled_time}:00",
    }), flush=True)


def schedule_album_review(target_date, unposted_albums, draft, json_output):
    """Schedule the next album review on `target_date`. Returns True if scheduled."""
    if not unposted_albums:
        print(f"[fill] {target_date}: no more unposted albums")
        return False

    if has_slot_for_date(target_date, SLOT_ALBUM_REVIEW):
        print(f"[fill] {target_date} {SLOT_ALBUM_REVIEW}: slot already filled")
        return False

    album_path = unposted_albums.pop(0)
    slug = get_slug(album_path)

    print(f"\n[fill] {target_date} {SLOT_ALBUM_REVIEW} -> album_review: {slug}")
    album_data = parse_album_markdown(album_path)
    print(f"  [parse] {album_data['album']} by {album_data['artist']} ({album_data['total_songs']} songs)")

    video_path = ensure_endcard(album_path, slug, draft=draft)
    if not video_path:
        return False

    caption = generate_album_caption(album_data)
    insert_queue_entry(
        album_slug=slug,
        album_name=album_data["album"],
        artist=album_data["artist"],
        scheduled_date=target_date,
        scheduled_time=SLOT_ALBUM_REVIEW,
        video_path=video_path,
        caption=caption,
        post_type="album_review",
        post_format="reel",
        stat_type=None,
    )
    advance_rotation()
    _emit_json(json_output, title=album_data["album"], video_path=video_path,
               caption=caption, scheduled_date=target_date, scheduled_time=SLOT_ALBUM_REVIEW)
    print(f"  [queued] {album_data['album']}")
    return True


def schedule_stats_day(target_date, artist_name, draft, json_output):
    """Schedule a stats_reel + stats_post on `target_date` for `artist_name`.

    Returns True if both were scheduled, False if no unused stat types or assets failed.
    """
    if not artist_name:
        print(f"[fill] {target_date}: no prior album_review to derive stats artist from")
        return False

    stat_type = pick_unused_stat_type(artist_name)
    if not stat_type:
        print(f"[fill] {target_date}: all stat types used for {artist_name} — fallback to album_review")
        return False

    print(f"\n[fill] {target_date} -> stats_day: {artist_name} / {stat_type}")
    assets = ensure_stats_assets(artist_name, stat_type, draft=draft)
    if not assets:
        print(f"  [stats] asset generation failed — falling back to album_review")
        return False
    reel_path, post_path, item_count = assets

    # Slot 1: stats reel @ 14:00
    if not has_slot_for_date(target_date, SLOT_STATS_REEL):
        reel_caption = generate_stats_caption(artist_name, stat_type, item_count, "reel")
        insert_queue_entry(
            album_slug=f"stats-{stat_type}",
            album_name=f"{artist_name} stats: {stat_type}",
            artist=artist_name,
            scheduled_date=target_date,
            scheduled_time=SLOT_STATS_REEL,
            video_path=reel_path,
            caption=reel_caption,
            post_type="stats_reel",
            post_format="reel",
            stat_type=stat_type,
        )
        _emit_json(json_output, title=f"{artist_name} - {stat_type} (reel)",
                   video_path=reel_path, caption=reel_caption,
                   scheduled_date=target_date, scheduled_time=SLOT_STATS_REEL)

    # Slot 2: stats post (image) @ 20:00
    if not has_slot_for_date(target_date, SLOT_STATS_POST):
        post_caption = generate_stats_caption(artist_name, stat_type, item_count, "image")
        insert_queue_entry(
            album_slug=f"stats-{stat_type}",
            album_name=f"{artist_name} stats: {stat_type}",
            artist=artist_name,
            scheduled_date=target_date,
            scheduled_time=SLOT_STATS_POST,
            video_path=post_path,
            caption=post_caption,
            post_type="stats_post",
            post_format="image",
            stat_type=stat_type,
        )
        _emit_json(json_output, title=f"{artist_name} - {stat_type} (post)",
                   video_path=post_path, caption=post_caption,
                   scheduled_date=target_date, scheduled_time=SLOT_STATS_POST)

    record_stat_used(artist_name, stat_type)
    print(f"  [queued] stats day: {artist_name} / {stat_type}")
    return True


# ============================== Filler day ==============================

def schedule_filler_day(target_date, seed_artist, json_output):
    """Schedule a filler post on `target_date` whose artist comes from the same
    genre bucket as `seed_artist`. Returns True on success."""
    if not seed_artist:
        print(f"[fill] {target_date}: no prior album_review to seed filler genre")
        return False

    if has_slot_for_date(target_date, SLOT_FILLER):
        print(f"[fill] {target_date} {SLOT_FILLER}: filler slot already filled")
        return True  # already there, treat as scheduled

    # Avoid back-to-back same filler artist
    exclude = recent_filler_artists(limit=5)

    print(f"\n[fill] {target_date} {SLOT_FILLER} -> filler day (seed={seed_artist})")
    try:
        result = build_filler_post(seed_artist, exclude=exclude)
    except Exception as e:
        print(f"  [filler] generation failed: {e}")
        return False
    if not result:
        return False

    filler_artist = result["filler_artist"]
    video_path = result["video_path"]
    cap = result.get("caption") or {}
    hook = cap.get("hook", "")
    text = cap.get("caption", "")
    tags = cap.get("hashtags", [])
    caption = (text + ("\n\n" + " ".join(tags) if tags else "")).strip() or \
              f"{filler_artist} moments. (related to {seed_artist})"

    insert_queue_entry(
        album_slug=f"filler-{re.sub(r'[^a-z0-9]+', '-', filler_artist.lower()).strip('-')}",
        album_name=f"{filler_artist} (filler / {seed_artist})",
        artist=filler_artist,
        scheduled_date=target_date,
        scheduled_time=SLOT_FILLER,
        video_path=video_path,
        caption=caption,
        post_type="filler",
        post_format="reel",
        stat_type=None,
    )
    _emit_json(json_output, title=f"{filler_artist} filler",
               video_path=video_path, caption=caption,
               scheduled_date=target_date, scheduled_time=SLOT_FILLER)
    print(f"  [queued] filler: {filler_artist} (hook={hook!r})")
    return True


# ============================== Main loop ==============================

def fill_queue(days_ahead=None, draft=False, json_output=False):
    """Fill the queue following the alternating schedule.

    `days_ahead=None` (default): AUTO mode — keep filling until the queue runs
    out of unposted albums AND the current artist's stats+filler cycle is done.

    `days_ahead=N`: hard cap; stop after N days regardless of catalog.
    """
    init_db()
    albums = get_albums()
    if not albums:
        print("No album files found in inputs/")
        return

    print(f"[scan] Found {len(albums)} albums: {[get_slug(a) for a in albums]}")

    # Filter to unposted
    unposted = []
    already_done = []
    for album_path in albums:
        slug = get_slug(album_path)
        if is_album_queued_or_posted(slug):
            already_done.append(slug)
        else:
            unposted.append(album_path)

    if already_done:
        print(f"[scan] Already queued/posted ({len(already_done)}): {already_done}")
    print(f"[scan] Unposted ({len(unposted)}): {[get_slug(a) for a in unposted]}")

    today = datetime.now().date()
    if days_ahead is None:
        print(f"\n[fill] Auto mode: filling until queue runs out of unposted albums "
              f"(starting from {today + timedelta(days=1)})")
    else:
        print(f"\n[fill] Filling {days_ahead} days starting from {today + timedelta(days=1)}")

    added = 0
    skipped_dates = []
    errors = []

    # Hard upper bound — protects against accidental infinite loops if a fallback
    # is silently failing every day. 365 days is more than any realistic backlog.
    MAX_DAYS_HARD_CAP = 365

    day_offset = 0
    while True:
        # Stop conditions:
        #   1. Hard limit (--days N) reached.
        #   2. Auto mode: no more unposted albums AND the next slot is album_review
        #      (i.e. the previous artist's stats+filler are done; nothing left to start).
        if days_ahead is not None and day_offset >= days_ahead:
            break
        if day_offset >= MAX_DAYS_HARD_CAP:
            print(f"[fill] safety cap hit at {MAX_DAYS_HARD_CAP} days, stopping")
            break

        last_entry = get_last_scheduled_entry()
        next_type = _next_post_type(last_entry)

        if days_ahead is None and not unposted and next_type == "album_review":
            print("[fill] auto mode: out of unposted albums and cycle complete — done")
            break

        target_date = (today + timedelta(days=day_offset + 1)).strftime("%Y-%m-%d")

        if next_type == "stats_day":
            artist = get_last_album_review_artist()
            ok = schedule_stats_day(target_date, artist, draft=draft, json_output=json_output)
            if ok:
                added += 2  # reel + post
                day_offset += 1
                continue
            # Fallback: schedule an album_review on this day instead
            print(f"[fill] {target_date}: stats_day fallback -> album_review")
            ok = schedule_album_review(target_date, unposted, draft=draft, json_output=json_output)
            if ok:
                added += 1
            else:
                skipped_dates.append(target_date)

        elif next_type == "filler_day":
            seed = get_last_album_review_artist()
            ok = schedule_filler_day(target_date, seed, json_output=json_output)
            if ok:
                added += 1
                day_offset += 1
                continue
            # Fallback: filler couldn't be built (TikTok scrape failed, etc.)
            print(f"[fill] {target_date}: filler_day fallback -> album_review")
            ok = schedule_album_review(target_date, unposted, draft=draft, json_output=json_output)
            if ok:
                added += 1
            else:
                skipped_dates.append(target_date)

        else:  # album_review
            ok = schedule_album_review(target_date, unposted, draft=draft, json_output=json_output)
            if ok:
                added += 1
            else:
                # Auto mode: a failed album_review with empty unposted is the natural stop
                if days_ahead is None and not unposted:
                    print(f"[fill] {target_date}: no unposted albums left — stopping")
                    break
                skipped_dates.append(target_date)

        day_offset += 1

    counts = get_pending_count()
    buffer = get_buffer_days()
    print(f"\n{'='*40}")
    print(f"Added {added} entries to queue across {day_offset} day(s)")
    if skipped_dates:
        print(f"Skipped {len(skipped_dates)} dates: {skipped_dates}")
    if errors:
        print(f"Errors ({len(errors)}): {errors}")
    print(f"Queue: {counts}")
    print(f"Buffer: {buffer} days")


def main():
    parser = argparse.ArgumentParser(description="Fill posting queue with alternating album/stats schedule")
    parser.add_argument("--days", type=int, default=None,
                        help="Days ahead to fill. Default: AUTO — fills until inputs/ runs "
                             "out of unposted albums and the current cycle completes.")
    parser.add_argument("--draft", action="store_true", help="Draft-quality renders (faster)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON lines for Flask scheduler integration")
    args = parser.parse_args()
    fill_queue(days_ahead=args.days, draft=args.draft, json_output=args.json)


if __name__ == "__main__":
    main()
