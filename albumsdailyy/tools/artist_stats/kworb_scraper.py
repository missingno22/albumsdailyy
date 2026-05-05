"""
Scrape per-track Spotify stream counts from kworb.net.

Kworb URL format:
    https://kworb.net/spotify/artist/{spotify_artist_id}_songs.html

Each row in the songs table:
    <tr><td class="text"><div>[* ] <a href="https://open.spotify.com/track/{TRACK_ID}">TITLE</a></div></td>
        <td>{STREAMS}</td><td>{DAILY}</td></tr>

The '* ' prefix marks "as feature" tracks (artist is featured, not lead). We
expose both lead-only and including-features views.
"""

import os
import re
import time
import urllib.request

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".tmp", "kworb_cache"
)
CACHE_TTL_SECONDS = 24 * 3600  # refresh once a day

_ROW_RE = re.compile(
    r'<tr><td class="text"><div>(?P<feat>\*\s)?'
    r'<a href="https://open\.spotify\.com/track/(?P<id>[A-Za-z0-9]+)"[^>]*>'
    r'(?P<title>[^<]+)</a></div></td>'
    r'<td>(?P<streams>[\d,]+)</td>'
    r'<td>(?P<daily>[\d,]+)</td></tr>',
    re.IGNORECASE,
)


def _cache_path(artist_id):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{artist_id}_songs.html")


def _is_fresh(path):
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < CACHE_TTL_SECONDS


def _fetch(url, timeout=15):
    print(f"  [kworb] GET {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (albumsdailyy/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"kworb returned HTTP {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


def fetch_artist_songs_html(spotify_artist_id, force_refresh=False):
    """Fetch the kworb songs page for an artist (cached for 24h). Returns HTML string."""
    cache = _cache_path(spotify_artist_id)
    if not force_refresh and _is_fresh(cache):
        print(f"  [kworb] cache hit: {cache}", flush=True)
        with open(cache, "r", encoding="utf-8") as f:
            return f.read()

    url = f"https://kworb.net/spotify/artist/{spotify_artist_id}_songs.html"
    html = _fetch(url)
    with open(cache, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [kworb] cached -> {cache}", flush=True)
    return html


def parse_song_rows(html):
    """Parse kworb song rows. Returns list of dicts: {track_id, title, streams, daily, is_feature}."""
    rows = []
    for m in _ROW_RE.finditer(html):
        rows.append({
            "track_id": m.group("id"),
            "title": m.group("title").strip(),
            "streams": int(m.group("streams").replace(",", "")),
            "daily": int(m.group("daily").replace(",", "")),
            "is_feature": bool(m.group("feat")),
        })
    return rows


def get_stream_counts(spotify_artist_id, include_features=False, force_refresh=False):
    """Return {track_id: stream_count} for an artist's tracks on kworb."""
    html = fetch_artist_songs_html(spotify_artist_id, force_refresh=force_refresh)
    rows = parse_song_rows(html)
    print(f"  [kworb] parsed {len(rows)} rows", flush=True)

    counts = {}
    for r in rows:
        if r["is_feature"] and not include_features:
            continue
        # Keep highest count if a track id appears twice (shouldn't, but defensive)
        if r["track_id"] not in counts or r["streams"] > counts[r["track_id"]]:
            counts[r["track_id"]] = r["streams"]
    return counts


def format_streams(n):
    """Format a stream count for display: 45,872,103 -> '45.9m'. <1m uses 'k'."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}b"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


if __name__ == "__main__":
    import sys
    artist_id = sys.argv[1] if len(sys.argv) > 1 else "5K4W6rqBFWDnAN6FQUkS6x"  # Kanye
    counts = get_stream_counts(artist_id)
    print(f"Got {len(counts)} tracks. Top 5:")
    top5 = sorted(counts.items(), key=lambda x: -x[1])[:5]
    for tid, s in top5:
        print(f"  {tid}: {format_streams(s):>8}  ({s:,})")
