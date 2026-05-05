"""
Detect rising trends from TikTok Creative Center + Google Trends.

Usage:
    python soylox/tools/detect_trends.py --limit 10
    python soylox/tools/detect_trends.py --limit 10 --region US

Emits ranked trends to .tmp/trends_YYYYMMDD_HHMM.json and prints the path on stdout.

Scoring: velocity-based (growth %, not raw views). Recently-emerged trends with
high publish acceleration rank higher than saturated trends with big totals.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SOYLOX_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(SOYLOX_ROOT, ".tmp")
ARCHIVE = os.path.join(SOYLOX_ROOT, "inputs", "archive.txt")


def _slugify(label):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", label.lower()).strip("-")
    return s[:60] or "trend"


def _load_archive():
    if not os.path.exists(ARCHIVE):
        return set()
    with open(ARCHIVE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def scrape_tiktok_creative_center(region="US", limit=50):
    """
    Scrape TikTok Creative Center trending hashtags via Playwright.
    Returns list of dicts: {slug, source, label, category, velocity, top_videos}.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[tiktok-cc] Playwright not installed — skipping. Install with: pip install playwright && playwright install chromium", flush=True)
        return []

    results = []
    url = f"https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en?period=7&countryCode={region}"

    print(f"[tiktok-cc] Scraping {url}", flush=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=45_000)
            time.sleep(3)

            # Expand the list by clicking "View More" a couple times
            for _ in range(3):
                try:
                    btn = page.query_selector("div[data-testid='cc_contentArea_viewmore_btn']")
                    if btn:
                        btn.click()
                        time.sleep(2)
                    else:
                        break
                except Exception:
                    break

            # Primary path: anchor links to hashtag pages contain the tag in the href
            anchors = page.query_selector_all("a[href*='/business/creativecenter/hashtag/']")
            print(f"[tiktok-cc] Found {len(anchors)} hashtag anchors", flush=True)

            seen_tags = set()
            for rank, a in enumerate(anchors):
                try:
                    href = a.get_attribute("href") or ""
                    # Extract tag from href: .../hashtag/<TAG>/pc/en or .../hashtag/<TAG>
                    m = re.search(r"/hashtag/([A-Za-z0-9_%\-]+)", href)
                    if not m:
                        continue
                    tag = m.group(1)
                    # Decode URL-encoded tags
                    try:
                        from urllib.parse import unquote
                        tag = unquote(tag)
                    except Exception:
                        pass
                    if not tag or tag in seen_tags:
                        continue
                    seen_tags.add(tag)

                    # Try to enrich from ancestor card text (growth %, posts count)
                    text = ""
                    try:
                        # walk up to nearest row/card
                        card_handle = a.evaluate_handle(
                            "el => el.closest('[class*=card],[class*=Card],[class*=item],[class*=Item]') || el.parentElement"
                        )
                        text = card_handle.evaluate("el => el ? el.innerText : ''") or ""
                    except Exception:
                        pass

                    growth_m = re.search(r"([+-]?\d[\d,.]*)\s*%", text)
                    growth = float(growth_m.group(1).replace(",", "")) if growth_m else 0.0

                    posts_m = re.search(r"([\d.,]+)\s*([KMB])", text)
                    posts = 0
                    if posts_m:
                        try:
                            num = float(posts_m.group(1).replace(",", ""))
                            mult = {"K": 1e3, "M": 1e6, "B": 1e9}[posts_m.group(2)]
                            posts = int(num * mult)
                        except Exception:
                            pass

                    # If no growth %, rank-based velocity so top-ranked still sort high
                    velocity = growth if growth > 0 else max(20.0, 300.0 - rank * 10)

                    results.append({
                        "slug": _slugify(tag),
                        "source": "tiktok_creative_center",
                        "label": tag,
                        "category": "hashtag",
                        "velocity": velocity,
                        "posts": posts,
                        "top_videos": [],
                        "search_query": f"#{tag}",
                    })
                    if len(results) >= limit:
                        break
                except Exception as e:
                    print(f"[tiktok-cc] Card parse failed: {e}", flush=True)
                    continue

            browser.close()
    except Exception as e:
        print(f"[tiktok-cc] Scrape failed: {e}", flush=True)
        return results

    print(f"[tiktok-cc] Got {len(results)} trending hashtags", flush=True)
    return results


def scrape_google_trends(region="US", limit=20):
    """
    Pull daily trending queries from Google Trends RSS feed.
    pytrends 4.9's API endpoints are broken by Google changes (Apr 2025+),
    but the RSS feed at trends.google.com/trending/rss remains stable.
    """
    import xml.etree.ElementTree as ET
    try:
        import requests
    except ImportError:
        print("[google-trends] requests not installed — skipping", flush=True)
        return []

    url = f"https://trends.google.com/trending/rss?geo={region}"
    print(f"[google-trends] Fetching RSS: {url}", flush=True)
    results = []

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            print(f"[google-trends] RSS returned {resp.status_code}", flush=True)
            return results

        root = ET.fromstring(resp.content)
        # RSS items: <item><title>...</title><ht:approx_traffic>...</ht:approx_traffic></item>
        ns = {"ht": "https://trends.google.com/trending/rss"}
        items = root.findall(".//item")
        for i, item in enumerate(items[:limit]):
            title_el = item.find("title")
            title = (title_el.text or "").strip() if title_el is not None else ""
            if not title:
                continue

            traffic_el = item.find("ht:approx_traffic", ns)
            traffic_txt = (traffic_el.text or "").strip() if traffic_el is not None else ""
            # Parse "50,000+" style
            traffic = 0
            m = re.search(r"([\d,]+)", traffic_txt)
            if m:
                try:
                    traffic = int(m.group(1).replace(",", ""))
                except Exception:
                    pass

            # Velocity: combine rank recency + traffic magnitude
            rank_weight = max(20.0, 200.0 - i * 8)
            traffic_boost = min(100.0, traffic / 5000.0)  # 500k+ traffic maxes at +100
            velocity = rank_weight + traffic_boost

            results.append({
                "slug": _slugify(title),
                "source": "google_trends_rss",
                "label": title,
                "category": f"traffic={traffic_txt or 'n/a'}",
                "velocity": velocity,
                "posts": traffic,
                "top_videos": [],
                "search_query": title,
            })
    except Exception as e:
        print(f"[google-trends] Fetch failed: {e}", flush=True)
        return results

    print(f"[google-trends] Got {len(results)} trending searches", flush=True)
    return results


def scrape_brainrot_subreddits():
    """
    Crawl Gen Z / Gen Alpha meme subreddits for hot video posts.
    Each post IS a trend — we don't need to abstract it to a hashtag and go
    re-find videos of that trend. Upvote velocity (upvotes/hour) serves as
    the trend score directly.

    Returns trend dicts with pre-populated `video_url` so the downstream
    fetcher can skip searching and yt-dlp the permalink directly.
    """
    try:
        import urllib.request
        import urllib.parse
    except ImportError:
        return []

    # Per-sub quota so small brainrot subs aren't buried by giant drama subs.
    # We take top-K per sub by upvote velocity, then merge. Tuning: brainrot core
    # gets the bulk of slots, TikTokCringe/memes get a couple as seasoning.
    # Sized for weekly batches (~28 reels) — plenty of headroom for dedup/failures.
    sub_quotas = {
        "brainrot": 12,          # on-brand core — bulk of slots
        "okbuddyretard": 8,      # peak Gen Z shitpost
        "GenZhumor": 8,
        "shitposting": 6,
        "memes": 4,
        "TikTokCringe": 3,       # drama sub — just a sprinkle
        "teenagers": 2,
    }
    video_domains = ("v.redd.it", "youtube", "youtu.be", "tiktok", "redgifs", "streamable")

    # Blocklist: terms that indicate real-world tragedy/politics/etc.
    # These sneak into r/TikTokCringe a lot and are wildly off-brand for a repost account.
    bad_kw = [
        "shooting", "shooter", "columbine", "killed", "murder", "suicide",
        "rape", "assault", "war", "hamas", "israel", "ukraine", "russia",
        "died", "dead body", "funeral", "execution", "genocide",
        "trump", "biden", "harris", "republican", "democrat", "election",
        "abortion", "nazi", "slur", "racist", "pedo", "epstein",
        "tragedy", "terrorist", "hostage",
    ]

    now_ts = time.time()
    # Collect per-sub results separately, then interleave at the end
    per_sub = {}
    for sub, quota in sub_quotas.items():
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=60"
        print(f"[reddit] r/{sub} hot.json (quota={quota})", flush=True)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "soylox-trend-fetcher/1.0 (by /u/anon)"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    print(f"[reddit] r/{sub} HTTP {resp.status}", flush=True)
                    continue
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[reddit] r/{sub} failed: {e}", flush=True)
            continue

        sub_candidates = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("stickied") or post.get("over_18"):
                continue
            is_vid = post.get("is_video")
            domain = post.get("domain", "") or ""
            if not (is_vid or any(domain.startswith(d) for d in video_domains)):
                continue

            permalink = post.get("permalink") or ""
            if not permalink:
                continue

            title = (post.get("title") or "").strip()
            title_lc = title.lower()
            if any(kw in title_lc for kw in bad_kw):
                print(f"[reddit]   blocked: {title[:70]}", flush=True)
                continue

            created = post.get("created_utc") or now_ts
            age_hours = max(0.5, (now_ts - created) / 3600.0)
            upvotes = int(post.get("ups") or post.get("score") or 0)
            velocity = upvotes / age_hours  # raw upvotes/hour — per-sub comparable

            post_id = post.get("id") or ""

            sub_candidates.append({
                "slug": _slugify(f"{sub}-{post_id}"),
                "source": f"reddit_r_{sub}",
                "label": title[:100] if title else f"r/{sub} post",
                "category": f"r/{sub} · {upvotes} upvotes · {age_hours:.1f}h old",
                "velocity": velocity,
                "posts": upvotes,
                "top_videos": [f"https://www.reddit.com{permalink}"],
                "search_query": title[:80],
                "video_url": f"https://www.reddit.com{permalink}",
                "meta_hint": {
                    "uploader": f"r/{sub}",
                    "title": title,
                    "description": title,
                },
            })

        # Take the top `quota` posts from this sub, sorted by in-sub velocity.
        sub_candidates.sort(key=lambda t: t["velocity"], reverse=True)
        per_sub[sub] = sub_candidates[:quota]
        print(f"[reddit] r/{sub} yielded {len(sub_candidates)} posts, taking top {len(per_sub[sub])}", flush=True)

    # Interleave: round-robin across subs in the order declared in sub_quotas.
    # This guarantees r/brainrot's top post ranks FIRST in the output, even though
    # its raw upvote velocity is smaller than r/TikTokCringe's (which is a huge sub).
    # We overwrite velocity with a descending synthetic score so the outer detect_trends()
    # re-sort preserves this interleaved order and ranks all Reddit trends above
    # TikTok CC / Google Trends fallbacks.
    results = []
    round_idx = 0
    SYNTHETIC_BASE = 100000.0  # well above any CC/Google velocity (~300)
    while True:
        added_this_round = 0
        for sub in sub_quotas.keys():
            if round_idx < len(per_sub.get(sub, [])):
                entry = per_sub[sub][round_idx]
                entry["velocity_raw"] = entry["velocity"]
                entry["velocity"] = SYNTHETIC_BASE - len(results)
                results.append(entry)
                added_this_round += 1
        if added_this_round == 0:
            break
        round_idx += 1

    print(f"[reddit] Returning {len(results)} interleaved posts (brainrot-first)", flush=True)
    return results


def detect_trends(limit=10, region="US"):
    """Merge, dedup, score, return top N trends."""
    os.makedirs(TMP_DIR, exist_ok=True)
    archived = _load_archive()
    print(f"[detect] Archive has {len(archived)} previously-posted slugs", flush=True)

    all_trends = []
    # Brainrot subs first — their posts come with video URLs pre-attached
    all_trends.extend(scrape_brainrot_subreddits())
    # Keep CC + Google Trends as secondary signal (they sometimes surface sound trends
    # that haven't hit Reddit yet); we'll normalize velocity cross-source below.
    all_trends.extend(scrape_tiktok_creative_center(region=region, limit=30))
    all_trends.extend(scrape_google_trends(region=region, limit=15))

    # Dedup by slug; keep highest-velocity source
    seen = {}
    for t in all_trends:
        if t["slug"] in archived:
            continue
        existing = seen.get(t["slug"])
        if not existing or t["velocity"] > existing["velocity"]:
            seen[t["slug"]] = t

    # Sort by velocity desc
    ranked = sorted(seen.values(), key=lambda t: t["velocity"], reverse=True)
    top = ranked[:limit]

    # Stamp detection time
    now = datetime.now().isoformat(timespec="seconds")
    for t in top:
        t["detected_at"] = now

    out_path = os.path.join(TMP_DIR, f"trends_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)

    print(f"[detect] Wrote {len(top)} trends to {out_path}", flush=True)
    for t in top:
        print(f"  [{t['source']}] {t['label']} (velocity={t['velocity']:.0f})", flush=True)
    return out_path, top


def main():
    parser = argparse.ArgumentParser(description="Detect rising trends for soylox")
    parser.add_argument("--limit", type=int, default=10, help="Max trends to emit")
    parser.add_argument("--region", default="US", help="Region code (default US)")
    args = parser.parse_args()

    out_path, trends = detect_trends(limit=args.limit, region=args.region)
    # Machine-readable final line: the output path
    print(f"TRENDS_PATH={out_path}", flush=True)
    sys.exit(0 if trends else 2)


if __name__ == "__main__":
    main()
