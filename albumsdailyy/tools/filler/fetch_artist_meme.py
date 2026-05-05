"""
Pull a meme/funny TikTok or Reddit clip featuring a given artist.

This is a slimmed-down replacement for piggybacking on soylox_fetch's tag-driven
flow. The hashtag-centric paths in soylox_fetch break for artist-name queries
(spaces in URLs, hardcoded `%23` prefix on TikTok search).

Strategy:
  1. TikTok web search by raw text query (no `#` prefix) — Playwright
  2. Reddit search scoped to music subreddits, exact-phrase match
  3. Reject any candidate whose title doesn't mention the artist's name
  4. Reject anything <3s or >90s

Returns the path to a downloaded mp4 + metadata, or None.
"""

import os
import re
import sys
import time
import json
import urllib.parse
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(PROJECT_ROOT)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "soylox", "tools"))

# Reuse soylox's yt-dlp wrapper — that part isn't tag-specific
import fetch_trend_videos as soylox_fetch  # noqa: E402

CACHE_ROOT = os.path.join(PROJECT_ROOT, ".tmp", "filler_clips")

# Curated artist-specific subreddits where the actual MEMES live (not music posts).
# Lookup is by lowercased artist name; falls back to a slugified guess if missing.
ARTIST_SUBREDDIT_MAP = {
    "kanye west": "Kanye",
    "drake": "Drizzy",
    "j. cole": "Jcole",
    "kendrick lamar": "KendrickLamar",
    "tyler, the creator": "tylerthecreator",
    "travis scott": "travisscott",
    "playboi carti": "playboicarti",
    "lil uzi vert": "liluzivert",
    "21 savage": "21savage",
    "future": "FutureTheRapper",
    "lil baby": "lilbaby",
    "gunna": "Gunna",
    "metro boomin": "MetroBoomin",
    "central cee": "CentralCee",
    "ice spice": "IceSpice",
    "the weeknd": "TheWeeknd",
    "sza": "SZA",
    "frank ocean": "FrankOcean",
    "bryson tiller": "brysontiller",
    "summer walker": "SummerWalker",
    "taylor swift": "TaylorSwift",
    "olivia rodrigo": "OliviaRodrigo",
    "billie eilish": "billieeilish",
    "doja cat": "DojaCat",
    "ariana grande": "ArianaGrande",
    "morgan wallen": "morganwallen",
    "zach bryan": "zachbryan",
    "jelly roll": "jellyroll",
    "tame impala": "TameImpala",
    "phoebe bridgers": "phoebebridgers",
}

# Music-focused subreddits — secondary fallback. Used only if artist-specific
# subreddit returns nothing.
MUSIC_SUBREDDITS = [
    "hiphopheads", "rap", "Music", "UrbanHipHopHeads",
    "realhiphop", "popheads", "indieheads", "rnb", "trap",
    "hiphop",
]

# Meme-aggregator subs — last-tier fallback for when the artist sub is dry.
MEME_SUBREDDITS = [
    "HipHopImages", "rapbattles", "Hiphopcirclejerk",
    "memes", "dankmemes",
]

# Suffixes tried in order — biased toward shitpost / meme content.
# Removed "interview" and "freestyle" since those return music-fan content,
# not memes.
QUERY_SUFFIXES = [
    "meme",
    "funny",
    "edit",
    "fancam",
    "reaction",
    "parody",
]

DURATION_MIN_SECONDS = 3
# Allow up to 30 min — orchestrator pre-trims to a 60s middle segment.
# Anything beyond this is usually a podcast / full episode and is unlikely to
# yield meme-grade content even after trimming.
DURATION_MAX_SECONDS = 1800


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "filler"


# --------------------------- TikTok search (raw text) ---------------------------

def find_via_tiktok_text_search(query, limit=5):
    """Scrape tiktok.com/search/video?q=<query> for video URLs (NO hashtag prefix)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    url = f"https://www.tiktok.com/search/video?q={urllib.parse.quote(query)}"
    print(f"  [filler-tiktok] {url}", flush=True)
    urls = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector("a[href*='/video/']", timeout=15_000)
            except Exception:
                pass
            for _ in range(3):
                page.mouse.wheel(0, 1500)
                time.sleep(1.0)
            anchors = page.query_selector_all("a[href*='/video/']")
            for a in anchors:
                href = a.get_attribute("href") or ""
                canon = soylox_fetch._canonical_tiktok_url(href)
                if canon and canon not in urls:
                    urls.append(canon)
                    if len(urls) >= limit:
                        break
            browser.close()
    except Exception as e:
        print(f"  [filler-tiktok] failed: {e}", flush=True)
    print(f"  [filler-tiktok] got {len(urls)} URLs", flush=True)
    return urls


# --------------------------- Google → TikTok URL extraction ---------------------------

def find_via_search_engine_tiktok(query, limit=8):
    """Use a search engine to find tiktok.com/@user/video/<id> URLs.

    Tries DuckDuckGo's HTML interface first (less aggressive bot detection
    than Google), falls back to Google. Returns canonical TikTok URLs.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    full_query = f"{query} site:tiktok.com"
    encoded = urllib.parse.quote(full_query)
    candidates = [
        ("ddg", f"https://html.duckduckgo.com/html/?q={encoded}"),
        ("google", f"https://www.google.com/search?q={encoded}&num=20"),
    ]

    urls = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"),
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            for engine_name, engine_url in candidates:
                if len(urls) >= limit:
                    break
                print(f"  [filler-tt/{engine_name}] {engine_url}", flush=True)
                try:
                    page.goto(engine_url, wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(1.0)
                    anchors = page.query_selector_all("a")
                    for a in anchors:
                        href = a.get_attribute("href") or ""
                        canon = soylox_fetch._canonical_tiktok_url(href)
                        if canon and canon not in urls:
                            urls.append(canon)
                            if len(urls) >= limit:
                                break
                    print(f"  [filler-tt/{engine_name}] cumulative {len(urls)} URLs", flush=True)
                except Exception as e:
                    print(f"  [filler-tt/{engine_name}] failed: {e}", flush=True)
            browser.close()
    except Exception as e:
        print(f"  [filler-tt] playwright failed: {e}", flush=True)
    print(f"  [filler-tt] total {len(urls)} TikTok URLs", flush=True)
    return urls


# Keep old name as a thin alias so other code that imports it doesn't break.
find_via_google_tiktok = find_via_search_engine_tiktok


# --------------------------- Reddit search (music-scoped) ---------------------------

def _reddit_search(url, artist_name, limit, scope_label):
    """Run a single Reddit search URL, post-filter for artist mention, return list of dicts."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "albumsdailyy-filler/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [filler-reddit/{scope_label}] failed: {e}", flush=True)
        return []

    out = []
    artist_l = artist_name.lower()
    first = artist_name.split()[0].lower()
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        is_video = post.get("is_video") or post.get("domain", "").startswith(
            ("v.redd.it", "youtube", "youtu.be", "tiktok", "redgifs", "streamable")
        )
        if not is_video or not _is_post_safe(post):
            continue
        title_l = (post.get("title") or "").lower()
        # Require the artist name (or its first word) in the title
        if artist_l not in title_l and (len(first) < 3 or first not in title_l):
            continue
        permalink = post.get("permalink")
        if permalink:
            out.append({
                "url": f"https://www.reddit.com{permalink}",
                "title": post.get("title") or "",
            })
            if len(out) >= limit:
                break
    print(f"  [filler-reddit/{scope_label}] {len(out)} relevant posts", flush=True)
    return out


def _is_post_safe(post):
    """Reject NSFW-flagged posts and obvious thirst-content titles."""
    if post.get("over_18"):
        return False
    title_l = (post.get("title") or "").lower()
    bad_keywords = ("nsfw", "thicc", "thiccness", "thirst", "fap",
                    "lewd", "horny", "gooning", "onlyfans")
    return not any(kw in title_l for kw in bad_keywords)


def _reddit_search_lax(url, limit, scope_label):
    """Like _reddit_search but skips the artist-name title check.

    Use this when the URL is already scoped to the artist (e.g. r/<artist>/top.json).
    NSFW + thirst-content posts are still filtered.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "albumsdailyy-filler/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [filler-reddit/{scope_label}] failed: {e}", flush=True)
        return []

    out = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        is_video = post.get("is_video") or post.get("domain", "").startswith(
            ("v.redd.it", "youtube", "youtu.be", "tiktok", "redgifs", "streamable")
        )
        if not is_video or not _is_post_safe(post):
            continue
        permalink = post.get("permalink")
        if permalink:
            out.append({
                "url": f"https://www.reddit.com{permalink}",
                "title": post.get("title") or "",
                "score": post.get("score", 0),
            })
            if len(out) >= limit:
                break
    print(f"  [filler-reddit/{scope_label}] {len(out)} safe video posts", flush=True)
    return out


def _artist_subreddit(artist_name):
    """Return the canonical artist subreddit name, or a slugified guess."""
    key = artist_name.strip().lower()
    if key in ARTIST_SUBREDDIT_MAP:
        return ARTIST_SUBREDDIT_MAP[key]
    # Slugified guess: "Tyler, The Creator" -> "tylerthecreator"
    return re.sub(r"[^A-Za-z0-9]", "", artist_name)


def find_via_reddit_music(artist_name, suffix, limit=8):
    """Reddit search, prioritizing the artist's own fan subreddit (where memes
    actually live), then music subreddits, then global Reddit.

    Returns dicts with optional `trusted` flag — clips from r/<artist> are
    trusted to be artist-relevant even if title doesn't repeat the name.
    """
    quoted = f'"{artist_name}"'
    query_parts = []
    if suffix:
        query_parts.append(suffix)
    query = " ".join(query_parts) if query_parts else quoted
    encoded = urllib.parse.quote(query)

    # 0) Artist-specific subreddit (memes goldmine).
    # First pass: keyword-filtered search.
    artist_sub = _artist_subreddit(artist_name)
    artist_url = (
        f"https://www.reddit.com/r/{artist_sub}/search.json"
        f"?q={urllib.parse.quote(suffix or 'meme')}"
        f"&restrict_sr=1&sort=top&t=year&limit=50"
    )
    print(f"  [filler-reddit] artist sub: r/{artist_sub} q={suffix or 'meme'!r}", flush=True)
    out = _reddit_search(artist_url, artist_name, limit, scope_label=f"r/{artist_sub}")
    if out:
        for o in out:
            o["trusted"] = True
        return out

    # 0b) Artist subreddit, broad: just pull recent top video posts (no keyword filter).
    # The whole subreddit is about this artist, so any video is relevant; the
    # title-mentions-artist filter is intentionally LAX here (we let posts
    # through even without the artist name in the title since they're already
    # in r/<artist>).
    broad_url = (
        f"https://www.reddit.com/r/{artist_sub}/top.json"
        f"?t=month&limit=50"
    )
    print(f"  [filler-reddit] artist sub broad: r/{artist_sub}/top.json", flush=True)
    out = _reddit_search_lax(broad_url, limit, scope_label=f"r/{artist_sub}/top")
    if out:
        for o in out:
            o["trusted"] = True
        return out

    # 1) Music subreddits — restricted search with full quoted query
    full_query = f'{quoted} {suffix}'.strip() if suffix else quoted
    encoded_full = urllib.parse.quote(full_query)
    sr_filter = "+".join(MUSIC_SUBREDDITS)
    music_url = (
        f"https://www.reddit.com/r/{sr_filter}/search.json"
        f"?q={encoded_full}&restrict_sr=1&sort=hot&t=year&limit=50"
    )
    print(f"  [filler-reddit] music subs q={full_query!r}", flush=True)
    out = _reddit_search(music_url, artist_name, limit, scope_label="music")
    if out:
        return out

    # 2) Meme-aggregator subs — for shitposts that live outside artist subs
    meme_filter = "+".join(MEME_SUBREDDITS)
    meme_url = (
        f"https://www.reddit.com/r/{meme_filter}/search.json"
        f"?q={encoded_full}&restrict_sr=1&sort=top&t=year&limit=50"
    )
    print(f"  [filler-reddit] meme subs q={full_query!r}", flush=True)
    out = _reddit_search(meme_url, artist_name, limit, scope_label="meme")
    if out:
        return out

    # 3) Global Reddit — broadest, post-filter still gates by title
    global_url = (
        f"https://www.reddit.com/search.json"
        f"?q={encoded_full}&sort=hot&t=year&limit=50"
    )
    return _reddit_search(global_url, artist_name, limit, scope_label="global")


# --------------------------- Public entrypoint ---------------------------

def _title_mentions_artist(title, artist_name):
    """Loose check: does the title contain the artist's name (or first word)?"""
    if not title:
        return False
    title_l = title.lower()
    if artist_name.lower() in title_l:
        return True
    first = artist_name.split()[0].lower()
    if len(first) < 3:  # avoid e.g. "21" which would be too generic
        return False
    return first in title_l


def _is_usable_clip(meta, artist_name, trusted_source=False):
    """Apply duration + relevance gates.

    `trusted_source=True` skips the title-mentions-artist check — used when the
    clip came from an artist-scoped subreddit (r/<artist>) where the subreddit
    itself implies the artist, so titles like "New snippet" or "Coachella" are
    legit even without naming the artist.
    """
    duration = meta.get("duration")
    if duration is None:
        return True  # Sometimes yt-dlp doesn't fill duration; trust it
    if duration < DURATION_MIN_SECONDS or duration > DURATION_MAX_SECONDS:
        return False
    if trusted_source:
        return True
    title = meta.get("title") or ""
    description = meta.get("description") or ""
    return _title_mentions_artist(title, artist_name) or \
           _title_mentions_artist(description[:200], artist_name)


def fetch_meme_clip(artist_name, max_attempts_per_query=3):
    """Try queries in order; return the first relevant clip dict, else None."""
    print(f"\n[filler] Searching memes for: {artist_name}", flush=True)
    slug = _slug(artist_name)

    for suffix in QUERY_SUFFIXES:
        query = (artist_name + " " + suffix).strip()
        sub_slug = _slug(suffix) or "raw"
        out_dir = os.path.join(CACHE_ROOT, slug, sub_slug)
        os.makedirs(out_dir, exist_ok=True)

        print(f"\n[filler] === query: {query!r} ===", flush=True)

        # Gather candidate URLs.
        # Priority order chosen for shitpost quality:
        #   1. Google → TikTok (real shitposts live on TikTok; bypasses anti-bot)
        #   2. Artist subreddit (memes goldmine — Reddit returns these first now)
        #   3. TikTok web search (usually 0 due to bot wall, kept as a no-op)
        candidates = []
        for url in find_via_search_engine_tiktok(query, limit=max_attempts_per_query * 3):
            candidates.append({"url": url, "src": "tiktok-via-search", "trusted": False})
        for r in find_via_reddit_music(artist_name, suffix, limit=max_attempts_per_query * 3):
            candidates.append({
                "url": r["url"], "src": "reddit",
                "title": r.get("title"), "trusted": r.get("trusted", False),
            })
        for url in find_via_tiktok_text_search(query, limit=max_attempts_per_query):
            candidates.append({"url": url, "src": "tiktok-direct", "trusted": False})

        if not candidates:
            print(f"  [filler] no candidates for '{query}'", flush=True)
            continue

        # Dedupe
        seen = set()
        unique = []
        for c in candidates:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            unique.append(c)

        # Try each. For trusted (artist-subreddit) sources, skip the title check.
        for idx, cand in enumerate(unique):
            print(f"  [filler] download {idx+1}/{len(unique)} ({cand['src']}): {cand['url']}", flush=True)
            path, meta = soylox_fetch.download_video(cand["url"], out_dir, index=idx)
            if not path or not os.path.exists(path):
                continue
            trusted = bool(cand.get("trusted"))
            if not _is_usable_clip(meta, artist_name, trusted_source=trusted):
                duration = meta.get("duration")
                title = (meta.get("title") or "")[:80]
                print(f"    rejected (duration={duration}, title={title!r}, trusted={trusted})", flush=True)
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
            print(f"  [filler] picked: {path} (duration={meta.get('duration')})", flush=True)
            return {
                "path": path,
                "meta": meta,
                "url": cand["url"],
                "query": query,
                "source": cand["src"],
            }

    print(f"\n[filler] FAILED to find any relevant meme clip for {artist_name}", flush=True)
    return None


if __name__ == "__main__":
    name = " ".join(sys.argv[1:]) or "Drake"
    result = fetch_meme_clip(name)
    if result:
        print(f"\n[done] {result['path']}  src={result['source']}")
        print(f"  query: {result['query']}")
        print(f"  meta:  {(result['meta'].get('title') or '?')[:80]}")
    else:
        print("\n[fail] no clip downloaded")
        sys.exit(2)
