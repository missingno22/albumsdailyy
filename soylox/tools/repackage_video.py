"""
Light-touch repackage for reposting trending videos.

Transforms (all subtle — preserves the vibe, slips past IG dedup):
  - 9:16 reformat (blurred letterbox if source is wider; center crop if taller)
  - Mild speed nudge: 0.97x by default (vary per-call for variety)
  - Audio loudnorm (-14 LUFS, IG-friendly)
  - +faststart (IG container requirement)

Usage:
    python soylox/tools/repackage_video.py --in raw.mp4 --out packaged.mp4
    python soylox/tools/repackage_video.py --in raw.mp4 --out packaged.mp4 --speed 1.03
"""

import argparse
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WIDTH, HEIGHT, FPS = 1080, 1920, 30

# Hook font — use the bold/display font already committed for albumsdailyy.
# Falls back to a system font if missing.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
HOOK_FONT_CANDIDATES = [
    os.path.join(_PROJECT_ROOT, "albumsdailyy", "fonts", "CollegiateBlackFLF.ttf"),
    os.path.join(_PROJECT_ROOT, "albumsdailyy", "fonts", "RubikMonoOne.ttf"),
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
HOOK_FONT = next((p for p in HOOK_FONT_CANDIDATES if os.path.exists(p)), HOOK_FONT_CANDIDATES[-1])
HOOK_DURATION = 2.5  # seconds the burned-in hook stays on screen


def _run(cmd, timeout=600):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def probe_video(path):
    """Return dict with width, height, duration, has_audio."""
    rc, out = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ], timeout=30)
    if rc != 0:
        raise RuntimeError(f"ffprobe failed: {out[:300]}")
    data = json.loads(out)

    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "duration": float(data.get("format", {}).get("duration", 0)),
        "has_audio": has_audio,
    }


def _ffmpeg_escape_path(p):
    """Escape a filesystem path for use inside ffmpeg filter graph args."""
    p = p.replace("\\", "/")
    p = p.replace(":", r"\:")
    return p


def _hook_fontsize(text):
    """Pick a fontsize that will fit within frame width (1080 minus padding).

    The CollegiateBlackFLF font at point-size X takes roughly 0.58*X pixels
    per char in all-caps — so for ~1000px of usable width we need:
        max_px / (0.58 * chars) ~= size
    Values tuned empirically. Shorter hook → bigger + more stop-scroll punch.
    """
    n = max(1, len(text))
    if n <= 10:
        return 112
    if n <= 16:
        return 92
    if n <= 22:
        return 76
    if n <= 30:
        return 62
    return 52


def _build_hook_drawtext(hook_text_file, font_path, fontsize):
    """Build a drawtext filter spec that burns the hook on-screen for the
    first HOOK_DURATION seconds. Big white text w/ heavy black border,
    top-centered.
    """
    font_esc = _ffmpeg_escape_path(font_path)
    text_esc = _ffmpeg_escape_path(hook_text_file)
    # Border scales with fontsize so the stroke stays proportional.
    borderw = max(6, int(fontsize * 0.10))
    return (
        f"drawtext="
        f"fontfile='{font_esc}':"
        f"textfile='{text_esc}':"
        f"fontcolor=white:"
        f"fontsize={fontsize}:"
        f"borderw={borderw}:"
        f"bordercolor=black:"
        f"x=(w-text_w)/2:"
        f"y=260:"
        f"line_spacing=8:"
        f"enable='lt(t,{HOOK_DURATION})'"
    )


def build_filter_chain(probe, speed, hook_drawtext=None):
    """Build ffmpeg -filter_complex spec for [v] and [a] outputs."""
    w, h = probe["width"], probe["height"]

    # Vertical 9:16 reformat
    target_ratio = WIDTH / HEIGHT  # ~0.5625
    src_ratio = (w / h) if h else target_ratio

    if abs(src_ratio - target_ratio) < 0.05:
        # Already vertical, just scale
        v_filter = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    elif src_ratio > target_ratio:
        # Landscape/square source — blurred letterbox background + foreground
        v_filter = (
            f"[0:v]split=2[vbg][vfg];"
            f"[vbg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},gblur=sigma=20,eq=brightness=-0.1[bg];"
            f"[vfg]scale={WIDTH}:-1:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        # Taller than 9:16 — center crop height to fit
        v_filter = f"scale={WIDTH}:-1:force_original_aspect_ratio=decrease,crop={WIDTH}:{HEIGHT}:0:(ih-{HEIGHT})/2"

    # Apply speed nudge (setpts inverse of speed)
    speed_filter = f"setpts=PTS/{speed}"

    # Optional burned-in hook (big top-center text for first ~2.5s)
    hook_part = f",{hook_drawtext}" if hook_drawtext else ""

    # Single filter_graph form — if we used split (landscape), it's multi-input
    if "split=2" in v_filter:
        # Append the speed filter after the overlay
        v_chain = f"{v_filter},{speed_filter},fps={FPS}{hook_part},format=yuv420p[v]"
    else:
        v_chain = f"[0:v]{v_filter},{speed_filter},fps={FPS}{hook_part},format=yuv420p[v]"

    if probe["has_audio"]:
        # atempo accepts 0.5-2.0 only; our range (0.95-1.05) is fine single-step
        a_chain = f"[0:a]atempo={speed},loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    else:
        a_chain = None

    return v_chain, a_chain


def repackage(src, dst, speed=0.97, hook_text=None):
    """Run the ffmpeg pipeline.

    If `hook_text` is provided, burns it in as large top-centered text for
    the first HOOK_DURATION seconds — the single biggest lever for stopping
    the scroll on IG Reels.
    """
    if not os.path.exists(src):
        raise FileNotFoundError(src)

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    probe = probe_video(src)
    print(f"[repack] src: {probe['width']}x{probe['height']} {probe['duration']:.1f}s audio={probe['has_audio']}", flush=True)

    # Write hook to a temp textfile — drawtext reads it as UTF-8 and this
    # sidesteps all the filter-graph quote/colon/percent escaping landmines.
    hook_drawtext = None
    hook_file = None
    if hook_text:
        safe_hook = hook_text.strip()
        if safe_hook:
            hook_file = dst + ".hook.txt"
            with open(hook_file, "w", encoding="utf-8") as f:
                f.write(safe_hook)
            size = _hook_fontsize(safe_hook)
            hook_drawtext = _build_hook_drawtext(hook_file, HOOK_FONT, size)
            print(f"[repack] hook: '{safe_hook}' (size={size})", flush=True)

    v_chain, a_chain = build_filter_chain(probe, speed, hook_drawtext=hook_drawtext)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", src,
        "-filter_complex", (v_chain + (";" + a_chain if a_chain else "")),
        "-map", "[v]",
    ]
    if a_chain:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-movflags", "+faststart",
        dst,
    ]

    try:
        rc, out = _run(cmd, timeout=600)
        if rc != 0:
            raise RuntimeError(f"ffmpeg repackage failed: {out[-500:]}")
    finally:
        if hook_file and os.path.exists(hook_file):
            try:
                os.remove(hook_file)
            except OSError:
                pass

    size_mb = os.path.getsize(dst) / 1024 / 1024
    print(f"[repack] dst: {dst} ({size_mb:.2f} MB)", flush=True)
    return dst


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", dest="dst", required=True)
    parser.add_argument("--speed", type=float, default=0.97,
                        help="Playback speed multiplier (0.95-1.05 typical)")
    parser.add_argument("--hook", default=None,
                        help="Short on-screen hook text for first ~2.5s")
    args = parser.parse_args()
    repackage(args.src, args.dst, speed=args.speed, hook_text=args.hook)


if __name__ == "__main__":
    main()
