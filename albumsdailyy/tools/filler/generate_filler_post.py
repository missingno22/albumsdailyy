"""
Generate a filler/shit post: TikTok meme clip of a popular artist in the same
genre as the most recent album-review artist.

Pipeline:
  1. Look up genres for the seed artist (Spotify)
  2. Pick a filler artist from albumsdailyy/tools/filler/genre_artists.yaml
  3. Scrape TikTok / Reddit for a meme clip of that artist (soylox stack)
  4. Repackage to 9:16, mild speed nudge, loudnorm (soylox/repackage_video.py)
  5. Generate caption via OpenAI (soylox/generate_caption.py)

Outputs:
  outputs/filler/<seed_artist_slug>__<filler_artist_slug>.mp4
  + sidecar .json with the generated caption / hashtags

Usage:
  python -m albumsdailyy.tools.filler.generate_filler_post "Kanye West"
  python -m albumsdailyy.tools.filler.generate_filler_post "Kanye West" --filler-artist "Travis Scott"
  python -m albumsdailyy.tools.filler.generate_filler_post "Kanye West" --speed 1.03
"""

import argparse
import json
import os
import re
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(PROJECT_ROOT)
sys.path.insert(0, REPO_ROOT)

from albumsdailyy.tools.artist_stats.spotify_client import search_artist
from albumsdailyy.tools.filler.genre_lookup import detect_bucket, pick_filler_artist
from albumsdailyy.tools.filler.fetch_artist_meme import fetch_meme_clip


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "filler"


def _run(cmd, timeout=600):
    print(f"  $ {' '.join(cmd[:6])}{'...' if len(cmd) > 6 else ''}", flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


# Target reel duration after trim — longer source clips get cut to this length.
TARGET_DURATION_SECONDS = 60


def _pre_trim(in_path, target_seconds=TARGET_DURATION_SECONDS):
    """If the input is longer than target, trim to target seconds with re-encode.
    Picks the middle segment so we skip leading silence / outros.
    Returns the trimmed path (or in_path if no trim needed).
    """
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", in_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float(proc.stdout.strip())
    except Exception:
        return in_path

    if duration <= target_seconds + 1:
        return in_path

    # Take the middle segment — bias toward the "good part"
    start = max(0.0, (duration - target_seconds) / 2.0)
    out_path = os.path.splitext(in_path)[0] + f"__trimmed_{int(target_seconds)}s.mp4"
    print(f"  [trim] {duration:.1f}s -> {target_seconds}s starting at {start:.1f}s -> {out_path}",
          flush=True)
    # Stream copy (no re-encode) — repackage will re-encode anyway, so we just
    # need to chop to the right span. Way faster (seconds vs minutes).
    # `-ss` before `-i` does input seek (fast keyframe-aligned).
    rc = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-i", in_path,
        "-t", f"{target_seconds}",
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ], capture_output=True, text=True, timeout=300)
    if rc.returncode != 0 or not os.path.exists(out_path):
        print(f"  [trim] copy failed (rc={rc.returncode}); using original", flush=True)
        return in_path
    return out_path


def repackage(in_path, out_path, speed=0.97):
    """Run soylox/repackage_video.py to 9:16 + loudnorm + speed nudge."""
    repackager = os.path.join(REPO_ROOT, "soylox", "tools", "repackage_video.py")
    if not os.path.exists(repackager):
        raise RuntimeError(f"repackage_video.py not found at {repackager}")
    proc = _run([
        sys.executable, "-u", repackager,
        "--in", in_path,
        "--out", out_path,
        "--speed", str(speed),
    ], timeout=600)
    if proc.returncode != 0:
        print(proc.stdout[-1000:], flush=True)
        print(proc.stderr[-1000:], flush=True)
        raise RuntimeError(f"repackage failed (exit {proc.returncode})")
    if not os.path.exists(out_path):
        raise RuntimeError(f"repackage produced no file at {out_path}")
    return out_path


def generate_caption_for_filler(filler_artist, seed_artist, clip_meta):
    """Use soylox's caption generator. We synthesize a fake `trend` for the prompt."""
    captioner = os.path.join(REPO_ROOT, "soylox", "tools", "generate_caption.py")
    if not os.path.exists(captioner):
        return _fallback_caption(filler_artist, seed_artist)

    # Build a temporary trend file the soylox script can consume
    tmp_dir = os.path.join(PROJECT_ROOT, ".tmp", "filler_captions")
    os.makedirs(tmp_dir, exist_ok=True)
    trend_path = os.path.join(tmp_dir, f"{_slug(filler_artist)}_trend.json")
    trend = [{
        "slug": _slug(filler_artist),
        "label": f"{filler_artist} meme moments",
        "search_query": f"{filler_artist} meme",
        "source": "filler-artist",
    }]
    with open(trend_path, "w", encoding="utf-8") as f:
        json.dump(trend, f)

    meta_arg = json.dumps({
        "uploader": clip_meta.get("uploader", ""),
        "title": clip_meta.get("title", "")[:200],
        "description": clip_meta.get("description", "")[:400],
    })

    proc = _run([
        sys.executable, "-u", captioner,
        "--trend", trend_path, "--index", "0",
        "--meta", meta_arg,
    ], timeout=60)
    if proc.returncode != 0:
        print(f"  [filler] caption gen failed (exit {proc.returncode}); using fallback", flush=True)
        print(proc.stdout[-500:] + proc.stderr[-500:], flush=True)
        return _fallback_caption(filler_artist, seed_artist)

    # The script prints JSON to stdout — locate the JSON object
    out = proc.stdout.strip()
    try:
        # try last { ... } block
        m = re.search(r"\{[^{}]*?\"caption\"[^{}]*?\}", out, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            data = json.loads(out)
    except Exception:
        return _fallback_caption(filler_artist, seed_artist)

    return data


def _fallback_caption(filler_artist, seed_artist):
    tag = re.sub(r"[^a-z0-9]+", "", filler_artist.lower())
    return {
        "hook": f"{filler_artist.upper()} MOMENT",
        "caption": f"if you liked the {seed_artist} review you'll feel this one 🤝",
        "hashtags": ["#memepage", "#brainrot", "#memes", "#hiphop",
                     f"#{tag}", "#shitpost", "#explorepage", "#instareels"],
    }


def build_filler_post(seed_artist, override_filler=None, speed=0.97, exclude=()):
    """Main orchestrator. Returns dict with paths + caption, or None on failure."""

    print(f"\n[filler] Seed artist: {seed_artist}", flush=True)
    artist_obj = search_artist(seed_artist)
    if artist_obj is None:
        raise RuntimeError(f"Spotify search failed for {seed_artist}")

    spotify_genres = artist_obj.get("genres", [])
    bucket = detect_bucket(spotify_genres)
    print(f"[filler] Spotify genres={spotify_genres} -> bucket={bucket}", flush=True)

    # Pick filler artist
    if override_filler:
        filler_artist = override_filler
        print(f"[filler] Using override filler artist: {filler_artist}", flush=True)
    else:
        exclude_full = list(exclude) + [seed_artist]
        filler_artist, used_bucket = pick_filler_artist(
            spotify_genres, exclude=exclude_full,
        )
        if not filler_artist:
            raise RuntimeError("No filler artist available (yaml empty or all excluded)")
        print(f"[filler] Picked filler artist: {filler_artist} (bucket={used_bucket})", flush=True)

    # Scrape TikTok
    clip = fetch_meme_clip(filler_artist)
    if not clip:
        raise RuntimeError(f"Could not find a meme clip for {filler_artist}")

    # Pre-trim long clips (Reddit interviews/freestyles can be 2-4 min)
    print(f"\n[filler] Pre-trimming if needed...", flush=True)
    trimmed_path = _pre_trim(clip["path"])

    # Repackage (9:16 + speed nudge + loudnorm)
    out_dir = os.path.join(PROJECT_ROOT, "outputs", "filler")
    os.makedirs(out_dir, exist_ok=True)
    out_name = f"{_slug(seed_artist)}__{_slug(filler_artist)}.mp4"
    out_path = os.path.join(out_dir, out_name)
    print(f"\n[filler] Repackaging -> {out_path}", flush=True)
    repackage(trimmed_path, out_path, speed=speed)

    # Caption
    print(f"\n[filler] Generating caption...", flush=True)
    caption_data = generate_caption_for_filler(filler_artist, seed_artist, clip["meta"])

    # Sidecar
    sidecar_path = os.path.splitext(out_path)[0] + ".json"
    sidecar = {
        "seed_artist": seed_artist,
        "filler_artist": filler_artist,
        "spotify_genres": spotify_genres,
        "bucket": bucket,
        "source_url": clip.get("url"),
        "source_meta": clip.get("meta"),
        "caption": caption_data,
        "video_path": out_path,
    }
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, ensure_ascii=False)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"\n[filler] DONE -> {out_path} ({size_mb:.1f} MB)", flush=True)
    print(f"[filler] Sidecar -> {sidecar_path}", flush=True)
    return sidecar


def main():
    p = argparse.ArgumentParser()
    p.add_argument("seed_artist", help="The album-review artist whose genre seeds the pick")
    p.add_argument("--filler-artist", help="Override the auto-picked filler artist")
    p.add_argument("--speed", type=float, default=0.97, help="Speed nudge for repackage")
    args = p.parse_args()

    result = build_filler_post(
        args.seed_artist,
        override_filler=args.filler_artist,
        speed=args.speed,
    )
    if not result:
        sys.exit(2)


if __name__ == "__main__":
    main()
