"""
Fetch the top actual videos USING a trending hashtag.

Strategy (stops on first that yields videos):
  1. Scrape TikTok Creative Center hashtag detail page for top posts
  2. Playwright-scrape TikTok web search page for /video/<id> URLs
  3. yt-dlp the tag page directly (often broken, but sometimes works)
  4. yt-dlp YouTube Shorts search as last resort

Each candidate URL is then fed into yt-dlp to download the actual .mp4.
We want the REAL viral videos powering the trend, not generic search hits.

Usage:
    python soylox/tools/fetch_trend_videos.py --trend .tmp/trends_*.json --index 0
    python soylox/tools/fetch_trend_videos.py --tag 67challenge --max-videos 2
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SOYLOX_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(SOYLOX_ROOT, ".tmp")
TRENDS_DIR = os.path.join(TMP_DIR, "trends")


def _run(cmd, timeout=300):
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except FileNotFoundError as e:
        return -2, str(e)


def _canonical_tiktok_url(href):
    """Normalize any tiktok.com/@user/video/<id> URL found in an href."""
    m = re.search(r"https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+", href)
    if m:
        return m.group(0)
    m = re.search(r"/@([\w.-]+)/video/(\d+)", href)
    if m:
        return f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
    return None


def find_via_creative_center(tag, limit=5):
    """Scrape the Creative Center hashtag detail page for top posts."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    url = f"https://ads.tiktok.com/business/creativecenter/hashtag/{tag}/pc/en"
    print(f"[fetch-cc] {url}", flush=True)
    urls = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
            # domcontentloaded avoids networkidle hangs; we then wait explicitly for anchors
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector("a[href*='/video/']", timeout=15_000)
            except Exception:
                pass  # continue even if no selector matched
            time.sleep(2)

            anchors = page.query_selector_all("a[href*='/video/']")
            for a in anchors:
                href = a.get_attribute("href") or ""
                canon = _canonical_tiktok_url(href)
                if canon and canon not in urls:
                    urls.append(canon)
                    if len(urls) >= limit:
                        break
            browser.close()
    except Exception as e:
        print(f"[fetch-cc] Failed: {e}", flush=True)
    print(f"[fetch-cc] Got {len(urls)} TikTok URLs", flush=True)
    return urls


def find_via_tiktok_search(tag, limit=5):
    """Scrape tiktok.com/search for video URLs."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    url = f"https://www.tiktok.com/search/video?q=%23{tag}"
    print(f"[fetch-search] {url}", flush=True)
    urls = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Wait for video anchors OR a login wall
            try:
                page.wait_for_selector("a[href*='/video/']", timeout=15_000)
            except Exception:
                pass
            # Scroll to trigger lazy-load
            for _ in range(3):
                page.mouse.wheel(0, 1500)
                time.sleep(1.2)

            anchors = page.query_selector_all("a[href*='/video/']")
            for a in anchors:
                href = a.get_attribute("href") or ""
                canon = _canonical_tiktok_url(href)
                if canon and canon not in urls:
                    urls.append(canon)
                    if len(urls) >= limit:
                        break
            browser.close()
    except Exception as e:
        print(f"[fetch-search] Failed: {e}", flush=True)
    print(f"[fetch-search] Got {len(urls)} TikTok URLs", flush=True)
    return urls


def find_via_reddit(query, limit=5):
    """Reddit global search for video posts matching the trend term.
    Reddit's anon per-subreddit search is broken (returns 0 for most terms);
    the global /search.json endpoint still works and covers all meme subs."""
    try:
        import urllib.request
        import urllib.parse
    except ImportError:
        return []

    # Strip leading # — Reddit treats hashtags as literal chars, not tag markers
    clean_query = query.lstrip("#").strip()
    if not clean_query:
        return []

    # Global search — no restrict_sr. Returns crossposts from SquaredCircle, brainrot,
    # TikTokCringe, etc. without us having to hardcode the sub list.
    search_url = (
        f"https://www.reddit.com/search.json"
        f"?q={urllib.parse.quote(clean_query)}&sort=hot&t=week&limit=50"
    )
    post_urls = []
    try:
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "soylox-trend-fetcher/1.0 (by /u/anon)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                print(f"[fetch-reddit] HTTP {resp.status}", flush=True)
                return []
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[fetch-reddit] Request failed: {e}", flush=True)
        return []

    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        # Only accept posts that will actually yield a video via yt-dlp
        if post.get("is_video") or post.get("domain", "").startswith(
            ("v.redd.it", "youtube", "youtu.be", "tiktok", "redgifs", "streamable")
        ):
            permalink = post.get("permalink")
            if permalink:
                post_urls.append(f"https://www.reddit.com{permalink}")
                if len(post_urls) >= limit:
                    break

    print(f"[fetch-reddit] Got {len(post_urls)} Reddit video posts for '{clean_query}'", flush=True)
    return post_urls


def download_video(url, out_dir, index=0):
    """Download a single video URL with yt-dlp. Returns (path, metadata_dict) or (None, None)."""
    os.makedirs(out_dir, exist_ok=True)
    out_tmpl = os.path.join(out_dir, f"{index:02d}.%(ext)s").replace("\\", "/")
    info_tmpl = os.path.join(out_dir, f"{index:02d}.info.json")

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--quiet",
        "--no-playlist",
        "--ignore-errors",
        # bv*+ba = best video + best audio (required for Reddit's DASH streams
        # which split video/audio); falls back to combined /b for TikTok/YT
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "-o", out_tmpl,
        url,
    ]
    rc, out = _run(cmd, timeout=120)
    if rc != 0:
        print(f"[download] yt-dlp failed for {url}: {out[:200]}", flush=True)

    video_path = None
    for ext in ("mp4", "mov", "webm"):
        cand = os.path.join(out_dir, f"{index:02d}.{ext}")
        if os.path.exists(cand):
            video_path = cand
            break

    meta = {}
    if os.path.exists(info_tmpl):
        try:
            with open(info_tmpl, "r", encoding="utf-8") as f:
                info = json.load(f)
            meta = {
                "id": info.get("id"),
                "title": info.get("title") or info.get("description", "")[:100],
                "description": info.get("description", ""),
                "uploader": info.get("uploader") or info.get("uploader_id"),
                "duration": info.get("duration"),
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "webpage_url": info.get("webpage_url") or url,
            }
        except Exception:
            pass

    return video_path, meta


def fetch_for_trend(trend, max_videos=2):
    """Download up to max_videos real videos for the trend.
    Returns list of {path, meta} dicts.

    Fast path: if trend has a pre-populated `video_url` (from the Reddit
    brainrot-sub crawl), skip all searching and download it directly.
    """
    slug = trend["slug"]
    tag = trend.get("label", "").lstrip("#")
    query = trend.get("search_query") or trend.get("label") or slug

    slug_dir = os.path.join(TRENDS_DIR, slug)
    clips_dir = os.path.join(slug_dir, "clips")
    os.makedirs(slug_dir, exist_ok=True)
    os.makedirs(clips_dir, exist_ok=True)

    print(f"[fetch] Trend: {trend['label'][:60]} (source={trend.get('source')}, max_videos={max_videos})", flush=True)

    candidate_urls = []

    # Fast path: Reddit brainrot-sub crawl already handed us the video URL
    direct_url = trend.get("video_url")
    if direct_url:
        print(f"[fetch] Direct URL: {direct_url}", flush=True)
        candidate_urls.append(direct_url)

    # Search path: only for trends WITHOUT a pre-attached URL (TikTok CC / Google Trends)
    if not candidate_urls and tag:
        candidate_urls.extend(find_via_creative_center(tag, limit=max_videos * 3))
    if not candidate_urls and tag:
        candidate_urls.extend(find_via_tiktok_search(tag, limit=max_videos * 3))
    if not candidate_urls:
        reddit_query = trend.get("search_query") or tag or trend.get("label", "")
        if reddit_query:
            candidate_urls.extend(find_via_reddit(reddit_query, limit=max_videos * 3))

    # Dedupe preserving order
    seen = set()
    unique_urls = []
    for u in candidate_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    print(f"[fetch] {len(unique_urls)} unique candidate TikTok URLs", flush=True)

    downloaded = []
    for i, url in enumerate(unique_urls):
        if len(downloaded) >= max_videos:
            break
        print(f"[fetch] Downloading {i+1}/{len(unique_urls)}: {url}", flush=True)
        path, meta = download_video(url, clips_dir, index=len(downloaded))
        if path and os.path.exists(path):
            # Filter out clips longer than 90s (not Reel-appropriate)
            if meta.get("duration") and meta["duration"] > 90:
                print(f"[fetch]   Skipped (too long: {meta['duration']}s)", flush=True)
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
            downloaded.append({"path": os.path.abspath(path), "meta": meta, "url": url})

    # Last-resort YouTube Shorts fallback if TikTok yielded nothing.
    # Use ytsearch with "#shorts" term AND a match-filter that requires vertical dimensions.
    # Without the height>width filter, yt-dlp will happily return landscape full videos
    # that embarrass us on a 9:16 repack.
    if not downloaded:
        print(f"[fetch] TikTok yielded 0 downloads; falling back to YouTube Shorts", flush=True)
        search_term = tag if tag else query.lstrip("#").strip()
        yt_query = f"ytsearch{max_videos * 6}:{search_term} #shorts"
        tmpl = os.path.join(clips_dir, "yt_%(autonumber)02d.%(ext)s").replace("\\", "/")
        cmd = [
            "yt-dlp", "--no-warnings", "--quiet", "--no-playlist", "--ignore-errors",
            "-f", "mp4/best[ext=mp4]/best",
            "--match-filter", "duration >= 3 & duration <= 90 & aspect_ratio<1",
            "--playlist-items", f"1-{max_videos * 6}",
            "-o", tmpl,
            yt_query,
        ]
        _run(cmd, timeout=240)
        for f in sorted(os.listdir(clips_dir)):
            if f.startswith("yt_") and f.lower().endswith(".mp4"):
                if len(downloaded) >= max_videos:
                    break
                downloaded.append({
                    "path": os.path.abspath(os.path.join(clips_dir, f)),
                    "meta": {"title": trend["label"], "description": trend["label"],
                             "uploader": "youtube", "webpage_url": yt_query},
                    "url": yt_query,
                })

    # Write manifest
    manifest = {
        "slug": slug,
        "label": trend["label"],
        "tag": tag,
        "videos": downloaded,
    }
    manifest_path = os.path.join(slug_dir, "videos_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"[fetch] Trend {slug}: downloaded {len(downloaded)} video(s)", flush=True)
    return manifest_path, downloaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trend")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--tag")
    parser.add_argument("--slug")
    parser.add_argument("--max-videos", type=int, default=2)
    args = parser.parse_args()

    if args.trend:
        with open(args.trend, "r", encoding="utf-8") as f:
            trends = json.load(f)
        trend = trends[args.index]
    elif args.tag:
        slug = args.slug or re.sub(r"[^a-z0-9]+", "-", args.tag.lower()).strip("-")
        trend = {"slug": slug, "label": args.tag, "search_query": f"#{args.tag}"}
    else:
        parser.error("Provide --trend+--index or --tag")

    manifest_path, videos = fetch_for_trend(trend, max_videos=args.max_videos)
    print(f"MANIFEST_PATH={manifest_path}", flush=True)
    sys.exit(0 if videos else 2)


if __name__ == "__main__":
    main()
