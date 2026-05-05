"""
One-shot helper: re-download every queued soylox reel with audio and
overwrite the existing 9:16 mp4 in place.

Background: the first 28-reel batch was queued before the yt-dlp format
selector was fixed to grab Reddit's DASH audio stream. Files exist but
are silent. This script reconstructs the Reddit source URL from each
queued video's filename slug, re-downloads with audio, re-repackages,
and writes over the same path — keeping all queue entries (title,
caption, scheduled_datetime, approval state) untouched.

Delete this file after the one-time backfill.

Usage:
    python soylox/tools/_reaudio_queue.py
    python soylox/tools/_reaudio_queue.py --dry-run
"""

import argparse
import os
import shutil
import sqlite3
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SOYLOX_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(SOYLOX_ROOT)
TOOLS_DIR = os.path.join(SOYLOX_ROOT, "tools")
TMP_DIR = os.path.join(PROJECT_ROOT, ".tmp", "reaudio")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "queue.db")
ACCOUNT_ID = 2  # soylox

sys.path.insert(0, TOOLS_DIR)
from fetch_trend_videos import download_video  # noqa: E402
from repackage_video import repackage, probe_video  # noqa: E402

# Map the lowercased slug form back to the actual Reddit sub name.
# Must match the quotas in detect_trends.py:scrape_brainrot_subreddits.
SUB_NAME_MAP = {
    "brainrot": "brainrot",
    "okbuddyretard": "okbuddyretard",
    "genzhumor": "GenZhumor",
    "shitposting": "shitposting",
    "memes": "memes",
    "tiktokcringe": "TikTokCringe",
    "teenagers": "teenagers",
}


def _parse_slug(video_path):
    """Turn 'brainrot-1sohhm3.mp4' into ('brainrot', '1sohhm3')."""
    fname = os.path.splitext(os.path.basename(video_path))[0]
    if "-" not in fname:
        return None, None
    sub_slug, _, post_id = fname.rpartition("-")
    return sub_slug, post_id


def _reddit_url(sub_slug, post_id):
    real_sub = SUB_NAME_MAP.get(sub_slug)
    if not real_sub or not post_id:
        return None
    return f"https://www.reddit.com/r/{real_sub}/comments/{post_id}/"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--speed", type=float, default=0.97)
    args = parser.parse_args()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT id, video_path, status FROM queue "
        "WHERE account_id=? AND status IN ('pending','approved') "
        "ORDER BY scheduled_datetime",
        (ACCOUNT_ID,),
    )
    rows = cur.fetchall()
    con.close()

    print(f"[reaudio] {len(rows)} queue entries to process", flush=True)

    replaced = 0
    skipped = []
    failed = []

    for qid, video_path, status in rows:
        fname = os.path.basename(video_path)
        sub_slug, post_id = _parse_slug(video_path)
        url = _reddit_url(sub_slug, post_id)
        if not url:
            print(f"[reaudio] #{qid} SKIP unrecognized slug: {fname}", flush=True)
            skipped.append(qid)
            continue

        if not os.path.exists(video_path):
            print(f"[reaudio] #{qid} SKIP missing file: {video_path}", flush=True)
            skipped.append(qid)
            continue

        # Probe: if already has audio, skip.
        try:
            probe = probe_video(video_path)
            if probe.get("has_audio"):
                print(f"[reaudio] #{qid} OK already has audio: {fname}", flush=True)
                skipped.append(qid)
                continue
        except Exception as e:
            print(f"[reaudio] #{qid} probe failed ({e}); will re-download anyway", flush=True)

        print(f"\n[reaudio] #{qid} {status} — {fname}", flush=True)
        print(f"[reaudio]   source: {url}", flush=True)

        if args.dry_run:
            continue

        work_dir = os.path.join(TMP_DIR, f"{sub_slug}-{post_id}")
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

        try:
            dl_path, _meta = download_video(url, work_dir, index=0)
            if not dl_path or not os.path.exists(dl_path):
                print(f"[reaudio] #{qid} FAIL download", flush=True)
                failed.append(qid)
                continue

            # Verify audio this time
            dl_probe = probe_video(dl_path)
            if not dl_probe.get("has_audio"):
                print(f"[reaudio] #{qid} FAIL downloaded file still has no audio", flush=True)
                failed.append(qid)
                continue

            repackage(dl_path, video_path, speed=args.speed)
            replaced += 1
        except Exception as e:
            print(f"[reaudio] #{qid} FAIL: {e}", flush=True)
            failed.append(qid)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    print(f"\n{'='*60}", flush=True)
    print(f"[reaudio] replaced: {replaced}", flush=True)
    print(f"[reaudio] skipped:  {len(skipped)}  {skipped}", flush=True)
    print(f"[reaudio] failed:   {len(failed)}   {failed}", flush=True)


if __name__ == "__main__":
    main()
