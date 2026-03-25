"""
End-to-end: generate a full reel from album rankings and post it to Instagram.

Usage:
    python tools/create_and_post_reel.py <rankings.md> --caption "Your caption here"

Options:
    --caption TEXT          Caption for the Instagram post (required)
    --reel-type full|short  Which reel to generate (default: full)
    --skip-generate         Skip reel generation, just post the existing output
    --keep-drive-file       Don't delete the temporary Drive upload after posting
    --draft                 Generate reel in draft mode (fast, lower quality)

Pipeline:
    1. Parse markdown rankings
    2. Download audio clips
    3. Download B-Roll video
    4. Calculate timing
    5. Compose the reel
    6. Upload to Google Drive (for public URL)
    7. Post to Instagram via API
"""

import argparse
import os
import subprocess
import sys

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def run_step(step_num, total, description, command):
    """Run a pipeline step and handle errors."""
    print(f"\n{'='*50}")
    print(f"[{step_num}/{total}] {description}")
    print(f"{'='*50}")

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        shell=True,
    )

    if result.returncode != 0:
        print(f"\nError: Step {step_num} failed — {description}")
        print(f"Command: {command}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a reel and post it to Instagram"
    )
    parser.add_argument("rankings_file", help="Path to markdown file with album rankings")
    parser.add_argument("--caption", required=True, help="Caption for the Instagram post")
    parser.add_argument(
        "--reel-type",
        choices=["full", "short"],
        default="full",
        help="Type of reel to generate (default: full)",
    )
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="Skip reel generation, just post existing output",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Generate reel in draft mode (faster, lower quality)",
    )
    parser.add_argument(
        "--thumb-offset",
        type=int,
        default=1000,
        help="Thumbnail offset in milliseconds (default: 1000 — after title card fade-in)",
    )
    args = parser.parse_args()

    # Validate input
    if not args.skip_generate and not os.path.exists(args.rankings_file):
        print(f"Error: Rankings file not found: {args.rankings_file}")
        sys.exit(1)

    # Determine output file based on reel type
    if args.reel_type == "full":
        output_file = os.path.join(PROJECT_ROOT, ".tmp", "output", "reel_final.mp4")
        timing_script = "tools/full_reel/calculate_full_timing.py"
        compose_script = "tools/full_reel/compose_full_reel.py"
    else:
        output_file = os.path.join(PROJECT_ROOT, ".tmp", "output", "short_reel_final.mp4")
        timing_script = "tools/short_reel/calculate_short_timing.py"
        compose_script = "tools/short_reel/compose_short_reel.py"

    if not args.skip_generate:
        # Ensure directories exist
        for d in [".tmp/audio", ".tmp/broll", ".tmp/output"]:
            os.makedirs(os.path.join(PROJECT_ROOT, d), exist_ok=True)

        total_gen_steps = 5
        draft_flag = " --draft" if args.draft else ""

        # Step 1: Parse markdown
        run_step(1, total_gen_steps, "Parsing album rankings",
                 f"python tools/parse_markdown.py {args.rankings_file}")

        # Step 2: Download audio
        run_step(2, total_gen_steps, "Downloading audio clips",
                 "python tools/download_audio.py")

        # Step 3: Download B-Roll
        run_step(3, total_gen_steps, "Downloading B-Roll video",
                 "python tools/download_broll.py")

        # Step 4: Calculate timing
        run_step(4, total_gen_steps, "Calculating timing",
                 f"python {timing_script}")

        # Step 5: Compose reel
        run_step(5, total_gen_steps, "Composing reel",
                 f"python {compose_script}{draft_flag}")

    # Verify output exists
    if not os.path.exists(output_file):
        print(f"\nError: Reel not found at {output_file}")
        print("Run without --skip-generate to create it first.")
        sys.exit(1)

    file_size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"\nReel ready: {output_file} ({file_size_mb:.1f}MB)")

    # Re-encode for Instagram reel compatibility
    # Requirements: H.264 High profile, 9:16 aspect ratio (1080x1920), AAC audio
    # Videos with wrong codec (VP9) or wrong aspect ratio (square/landscape)
    # will either fail to post or post as a regular video instead of a reel.
    compat_file = output_file.replace(".mp4", "_compat.mp4")
    print(f"\n{'='*50}")
    print(f"Re-encoding for Instagram reel compatibility...")
    print(f"{'='*50}")

    # Probe input dimensions to determine padding
    probe_cmd = (
        f'ffprobe -v quiet -print_format json -show_streams "{output_file}"'
    )
    probe_result = subprocess.run(
        probe_cmd, cwd=PROJECT_ROOT, shell=True, capture_output=True, text=True
    )
    vf_filter = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
    if probe_result.returncode == 0:
        import json
        try:
            streams = json.loads(probe_result.stdout).get("streams", [])
            video = next((s for s in streams if s["codec_type"] == "video"), None)
            if video:
                w, h = int(video["width"]), int(video["height"])
                print(f"  Input: {w}x{h}")
                if w == 1080 and h == 1920:
                    # Already 9:16, just re-encode codec
                    vf_filter = ""
                    print(f"  Already 9:16 — re-encoding codec only")
                else:
                    print(f"  Will pad/scale to 1080x1920 (9:16)")
        except (json.JSONDecodeError, KeyError, StopIteration):
            print(f"  Could not probe dimensions, applying default 9:16 scaling")

    vf_flag = f'-vf "{vf_filter}" ' if vf_filter else ""
    reencode_cmd = (
        f'ffmpeg -y -i "{output_file}" '
        f'{vf_flag}'
        f'-c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p '
        f'-c:a aac -b:a 128k -movflags +faststart -r 30 "{compat_file}"'
    )
    result = subprocess.run(reencode_cmd, cwd=PROJECT_ROOT, shell=True, capture_output=True)
    if result.returncode == 0 and os.path.exists(compat_file):
        compat_size_mb = os.path.getsize(compat_file) / (1024 * 1024)
        print(f"  Re-encoded: {compat_size_mb:.1f}MB (was {file_size_mb:.1f}MB)")
        upload_file = compat_file
    else:
        print(f"  Warning: Re-encode failed, using original file")
        upload_file = output_file

    # Post to Instagram
    thumb_flag = f" --thumb-offset {args.thumb_offset}" if args.thumb_offset else ""
    post_cmd = (
        f'python tools/post_to_instagram.py reel "{upload_file}" '
        f'--caption "{args.caption}"{thumb_flag}'
    )

    print(f"\n{'='*50}")
    print(f"Posting to Instagram...")
    print(f"{'='*50}")

    result = subprocess.run(post_cmd, cwd=PROJECT_ROOT, shell=True)
    if result.returncode != 0:
        print("\nError: Instagram posting failed.")
        print(f"The reel is still available at: {output_file}")
        print("You can retry posting with:")
        print(f'  python tools/post_to_instagram.py "{output_file}" --caption "..."')
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"Done! Reel generated and posted to Instagram.")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
