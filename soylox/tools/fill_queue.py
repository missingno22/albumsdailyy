"""
Soylox trend-repost orchestrator.

Per run:
    detect_trends -> (for each trend)
        fetch_trend_videos -> repackage_video -> generate_caption
        -> emit JSON line for Flask queue

Schedules posts at fixed daily prime-time slots (see DAILY_PRIME_SLOTS),
rolling across as many days as needed to fit `--count` posts.

Usage (local test):
    python soylox/tools/fill_queue.py --count 1 --dry-run
    python soylox/tools/fill_queue.py --count 4 --json

Flask integration:
    Automation script command: python soylox/tools/fill_queue.py --json --count 4
"""

import argparse
import json
import os
import random
import sys
import traceback
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SOYLOX_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(SOYLOX_ROOT, "tools")
ARCHIVE = os.path.join(SOYLOX_ROOT, "inputs", "archive.txt")
REELS_DIR = os.path.join(SOYLOX_ROOT, "outputs", "reels")

sys.path.insert(0, TOOLS_DIR)

from detect_trends import detect_trends
from fetch_trend_videos import fetch_for_trend
from repackage_video import repackage
from generate_caption import generate_caption

# Gen Z / Gen Alpha IG prime-time slots (24h clock, local time).
# Dropped from 4 to 3/day — new accounts shouldn't over-post, and these
# are the three windows with the strongest documented IG engagement for
# the Gen Z demo (lunch scroll, post-work, pre-bed doomscroll).
DAILY_PRIME_SLOTS = [12, 18, 21]  # noon, 6pm, 9pm
# Speed pool — each repost gets a random value to defeat IG audio fingerprint
SPEED_POOL = [0.95, 0.96, 0.97, 1.03, 1.04, 1.05]


def _append_archive(slug):
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
    with open(ARCHIVE, "a", encoding="utf-8") as f:
        f.write(f"{slug}\n")


def _load_archive():
    if not os.path.exists(ARCHIVE):
        return set()
    with open(ARCHIVE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def _schedule_slots(count, slots_per_day=None):
    """Build `count` post slots at fixed daily prime times.

    Starts from the next valid prime slot (skips past slots + leaves a 15min
    review buffer). Rolls across as many days as needed to satisfy count.
    Example: count=28 with 4 slots/day → 7 days of posts at 11a/2p/6p/9p.
    """
    if slots_per_day is None:
        slots_per_day = DAILY_PRIME_SLOTS

    now = datetime.now()
    min_lead = now + timedelta(minutes=15)

    slots = []
    day_offset = 0
    while len(slots) < count and day_offset < 30:
        day = now.date() + timedelta(days=day_offset)
        for hour in slots_per_day:
            slot_dt = datetime(day.year, day.month, day.day, hour, 0, 0)
            if slot_dt > min_lead:
                slots.append(slot_dt)
                if len(slots) >= count:
                    break
        day_offset += 1
    return slots


def process_trend(trend, slot_dt, max_per_trend=1, dry_run=False):
    """Pipeline: fetch videos -> repackage -> caption -> return queue entries."""
    slug = trend["slug"]
    print(f"\n{'='*60}\n[pipeline] {slug}  |  {trend['label']}\n{'='*60}", flush=True)

    entries = []
    try:
        print("[pipeline] Step 1/3: fetch_trend_videos", flush=True)
        _, videos = fetch_for_trend(trend, max_videos=max_per_trend)
        if not videos:
            print("[pipeline] No videos downloaded, skipping trend", flush=True)
            return []

        os.makedirs(REELS_DIR, exist_ok=True)

        for i, v in enumerate(videos):
            src_path = v["path"]
            meta = v.get("meta", {})

            # Step order swapped: generate caption FIRST so we have the hook
            # text available to burn into the video during repackage.
            print("[pipeline] Step 2/3: generate_caption", flush=True)
            try:
                cap = generate_caption(trend, meta=meta)
            except Exception as e:
                print(f"[pipeline] Caption gen failed: {e}", flush=True)
                # Fallback — use trend label + default brainrot tags
                cap = {
                    "hook": "",
                    "caption": trend["label"],
                    "hashtags": ["#memepage", "#brainrot", "#memes", "#genz", "#dankmemes"],
                }

            print(f"\n[pipeline] Step 3/3: repackage_video ({i+1}/{len(videos)})", flush=True)
            speed = random.choice(SPEED_POOL)
            variant = f"{slug}_{i}" if len(videos) > 1 else slug
            dst_path = os.path.join(REELS_DIR, f"{variant}.mp4")
            try:
                repackage(src_path, dst_path, speed=speed, hook_text=cap.get("hook", ""))
            except Exception as e:
                print(f"[pipeline] Repackage failed: {e}", flush=True)
                continue

            full_caption = f"{cap['caption']}\n\n{' '.join(cap['hashtags'])}".strip()

            entries.append({
                "title": (meta.get("title") or trend["label"])[:80],
                "video_path": os.path.abspath(dst_path),
                "caption": full_caption,
                "scheduled_datetime": slot_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "_source_url": v.get("url"),
                "_speed": speed,
            })

        if entries and not dry_run:
            _append_archive(slug)

        return entries

    except Exception as e:
        print(f"[pipeline] FAILED for {slug}: {e}", flush=True)
        traceback.print_exc()
        return []


def fill_queue(count=4, max_per_trend=1, dry_run=False, json_output=False, region="US"):
    print(f"[fill] Soylox Trend-Repost | target={count} reels | region={region}", flush=True)

    trend_pool_size = count * 3  # Overfetch — some trends yield 0 videos
    print(f"\n[fill] Detecting {trend_pool_size} trend candidates...", flush=True)
    _, trends = detect_trends(limit=trend_pool_size, region=region)
    if not trends:
        print("[fill] No trends detected. Exiting.", flush=True)
        return

    slots = _schedule_slots(count)
    print(f"[fill] Scheduled slots: {[s.strftime('%m-%d %H:%M') for s in slots]}", flush=True)

    queued = []
    trend_iter = iter(trends)
    while len(queued) < count:
        try:
            trend = next(trend_iter)
        except StopIteration:
            print(f"[fill] Ran out of trend candidates at {len(queued)}/{count}", flush=True)
            break

        remaining = count - len(queued)
        slot = slots[len(queued)]
        new_entries = process_trend(trend, slot, max_per_trend=min(max_per_trend, remaining), dry_run=dry_run)

        # Reassign slots to additional entries from same trend
        for idx, e in enumerate(new_entries):
            if len(queued) >= count:
                break
            if idx > 0 and len(queued) < len(slots):
                e["scheduled_datetime"] = slots[len(queued)].strftime("%Y-%m-%dT%H:%M:%S")
            queued.append(e)

    print(f"\n{'='*60}\n[fill] DONE — produced {len(queued)}/{count} reels\n{'='*60}", flush=True)
    for e in queued:
        print(f"  {e['scheduled_datetime']}  |  {e['title']}  (speed={e['_speed']})", flush=True)

    if json_output and not dry_run:
        for e in queued:
            clean = {k: v for k, v in e.items() if not k.startswith("_")}
            print(json.dumps(clean, ensure_ascii=False), flush=True)
    elif dry_run:
        for e in queued:
            clean = {k: v for k, v in e.items() if not k.startswith("_")}
            print("[dry-run] " + json.dumps(clean, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Soylox trend-repost queue filler")
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--per-trend", type=int, default=1,
                        help="Max videos to queue per detected trend")
    parser.add_argument("--region", default="US")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    fill_queue(
        count=args.count,
        max_per_trend=args.per_trend,
        dry_run=args.dry_run,
        json_output=args.json,
        region=args.region,
    )


if __name__ == "__main__":
    main()
