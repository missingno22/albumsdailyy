"""
Post content to Instagram via the Instagram API with Instagram Login.

Supports reels (video) and carousel posts (multiple images).

Usage:
    # Post a reel
    python tools/post_to_instagram.py reel .tmp/output/reel_final.mp4 --caption "Caption"

    # Post a carousel of images
    python tools/post_to_instagram.py carousel .tmp/output/post_slide_1.png .tmp/output/post_slide_2.png --caption "Caption"

Pipeline:
    1. Uploads media to catbox.moe for a direct public URL
    2. Creates media container(s) on Instagram
    3. Polls until Instagram finishes processing
    4. Publishes the post

Requires:
    - .env with INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN
"""

import argparse
import json
import os
import sys
import time
import subprocess
import urllib.request
import urllib.error
import urllib.parse

INSTAGRAM_API_BASE = "https://graph.instagram.com/v25.0"

# Max time to wait for Instagram to process media (seconds)
MAX_POLL_TIME = 300
POLL_INTERVAL = 15


def load_env():
    """Load environment variables from .env file."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars


def upload_file(file_path):
    """Upload a file to catbox.moe and return a direct URL."""
    filename = os.path.basename(file_path)
    print(f"  Uploading {filename}...")

    result = subprocess.run(
        [
            "curl", "-s",
            "-F", "reqtype=fileupload",
            "-F", f"fileToUpload=@{file_path}",
            "https://catbox.moe/user/api.php",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  Upload failed: {result.stderr}")
        sys.exit(1)

    url = result.stdout.strip()
    if not url.startswith("http"):
        print(f"  Unexpected response: {url}")
        sys.exit(1)

    print(f"  -> {url}")
    return url


def instagram_api_request(endpoint, params=None, method="GET"):
    """Make a request to the Instagram API using urllib."""
    url = f"{INSTAGRAM_API_BASE}/{endpoint}"

    if method == "GET":
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
    elif method == "POST":
        data = urllib.parse.urlencode(params).encode("utf-8") if params else None
        req = urllib.request.Request(url, data=data, method="POST")

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_body)
            error_msg = error_json.get("error", {}).get("message", error_body)
        except json.JSONDecodeError:
            error_msg = error_body
        print(f"  Instagram API error ({e.code}): {error_msg}")
        sys.exit(1)


def wait_for_container(container_id, access_token, label="media"):
    """Poll Instagram until a container is processed."""
    elapsed = 0
    while elapsed < MAX_POLL_TIME:
        result = instagram_api_request(
            container_id,
            params={
                "fields": "id,status,status_code",
                "access_token": access_token,
            },
        )

        status = result.get("status_code", "UNKNOWN")
        print(f"  {label} status: {status} ({elapsed}s)")

        if status == "FINISHED":
            return True
        elif status == "ERROR":
            print(f"  Error response: {json.dumps(result, indent=2)}")
            sys.exit(1)

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    print(f"  Timed out after {MAX_POLL_TIME}s.")
    sys.exit(1)


def publish(user_id, access_token, container_id):
    """Publish a media container."""
    result = instagram_api_request(
        f"{user_id}/media_publish",
        params={
            "creation_id": container_id,
            "access_token": access_token,
        },
        method="POST",
    )
    return result["id"]


# ── Reel posting ─────────────────────────────────────────────

def post_reel(user_id, access_token, video_path, caption, thumb_offset=None):
    """Upload and post a reel to Instagram."""
    # Step 1: Upload
    print("\n[1/4] Uploading video...")
    video_url = upload_file(video_path)

    # Step 2: Create container
    print("\n[2/4] Creating reel container...")
    container_params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": access_token,
    }
    if thumb_offset is not None:
        container_params["thumb_offset"] = str(thumb_offset)
        print(f"  Thumbnail offset: {thumb_offset}ms")
    result = instagram_api_request(
        f"{user_id}/media",
        params=container_params,
        method="POST",
    )
    container_id = result["id"]
    print(f"  Container: {container_id}")

    # Step 3: Wait
    print("\n[3/4] Waiting for processing...")
    wait_for_container(container_id, access_token, "Reel")

    # Step 4: Publish
    print("\n[4/4] Publishing...")
    media_id = publish(user_id, access_token, container_id)
    print(f"  Published! Media ID: {media_id}")
    return media_id


# ── Carousel posting ─────────────────────────────────────────

def post_carousel(user_id, access_token, image_paths, caption):
    """Upload and post a carousel of images to Instagram."""
    total_steps = len(image_paths) + 3  # upload + children + container + publish
    step = 1

    # Step 1: Upload all images
    print(f"\n[{step}/{total_steps}] Uploading {len(image_paths)} images...")
    image_urls = []
    for path in image_paths:
        url = upload_file(path)
        image_urls.append(url)
    step += 1

    # Step 2: Create child containers for each image
    print(f"\n[{step}/{total_steps}] Creating carousel item containers...")
    child_ids = []
    for i, url in enumerate(image_urls):
        result = instagram_api_request(
            f"{user_id}/media",
            params={
                "image_url": url,
                "is_carousel_item": "true",
                "access_token": access_token,
            },
            method="POST",
        )
        child_id = result["id"]
        print(f"  Slide {i+1}: {child_id}")
        child_ids.append(child_id)
    step += 1

    # Step 3: Create carousel container
    print(f"\n[{step}/{total_steps}] Creating carousel container...")
    result = instagram_api_request(
        f"{user_id}/media",
        params={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": access_token,
        },
        method="POST",
    )
    container_id = result["id"]
    print(f"  Carousel container: {container_id}")

    # Wait for processing
    wait_for_container(container_id, access_token, "Carousel")
    step += 1

    # Step 4: Publish
    print(f"\n[{step}/{total_steps}] Publishing...")
    media_id = publish(user_id, access_token, container_id)
    print(f"  Published! Media ID: {media_id}")
    return media_id


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Post content to Instagram")
    subparsers = parser.add_subparsers(dest="type", required=True)

    # Reel subcommand
    reel_parser = subparsers.add_parser("reel", help="Post a video reel")
    reel_parser.add_argument("video_path", help="Path to the video file (.mp4)")
    reel_parser.add_argument("--caption", required=True, help="Caption for the post")
    reel_parser.add_argument("--thumb-offset", type=int, default=None,
                             help="Thumbnail offset in milliseconds from start of video")

    # Carousel subcommand
    carousel_parser = subparsers.add_parser("carousel", help="Post a multi-image carousel")
    carousel_parser.add_argument("image_paths", nargs="+", help="Paths to image files (2-10 images)")
    carousel_parser.add_argument("--caption", required=True, help="Caption for the post")

    args = parser.parse_args()

    # Load credentials
    env = load_env()
    user_id = env.get("INSTAGRAM_USER_ID")
    access_token = env.get("INSTAGRAM_ACCESS_TOKEN")

    if not user_id:
        print("Error: INSTAGRAM_USER_ID not set in .env")
        sys.exit(1)
    if not access_token:
        print("Error: INSTAGRAM_ACCESS_TOKEN not set in .env")
        sys.exit(1)

    if args.type == "reel":
        if not os.path.exists(args.video_path):
            print(f"Error: Video not found: {args.video_path}")
            sys.exit(1)
        file_size_mb = os.path.getsize(args.video_path) / (1024 * 1024)
        if file_size_mb > 100:
            print(f"Error: Video is {file_size_mb:.1f}MB — Instagram limit is 100MB")
            sys.exit(1)
        print(f"Posting reel: {args.video_path} ({file_size_mb:.1f}MB)")
        print(f"Caption: {args.caption[:80]}{'...' if len(args.caption) > 80 else ''}")
        media_id = post_reel(user_id, access_token, args.video_path, args.caption,
                             thumb_offset=args.thumb_offset)

    elif args.type == "carousel":
        if len(args.image_paths) < 2:
            print("Error: Carousel requires at least 2 images")
            sys.exit(1)
        if len(args.image_paths) > 10:
            print("Error: Carousel supports max 10 images")
            sys.exit(1)
        for path in args.image_paths:
            if not os.path.exists(path):
                print(f"Error: Image not found: {path}")
                sys.exit(1)
        print(f"Posting carousel: {len(args.image_paths)} images")
        print(f"Caption: {args.caption[:80]}{'...' if len(args.caption) > 80 else ''}")
        media_id = post_carousel(user_id, access_token, args.image_paths, args.caption)

    print(f"\n{'='*50}")
    print(f"Posted successfully!")
    print(f"  Media ID: {media_id}")


if __name__ == "__main__":
    main()
