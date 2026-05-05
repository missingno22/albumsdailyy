"""
Stat-type registry for artist stats posts.

Each stat type is a function (artist_data) -> dict:
    {
        "title": "Lowest Streamed Song on Each Album",
        "items": [
            {"album_name": ..., "album_image": <url>, "value_text": "45.9m",
             "label_text": "Breathe In Breathe Out"},
            ...
        ],
    }

`artist_data` is built once by `build_artist_data(name)` and contains:
    {
      "name", "spotify_id", "image",
      "albums": [
        {"id", "name", "release_year", "image",
         "tracks": [{"id", "name", "duration_ms", "streams"}, ...]
        }, ...
      ]
    }
"""

from . import spotify_client, kworb_scraper


# Tracks under this many seconds are usually skits/intros — exclude from "shortest"
MIN_SONG_SECONDS = 60

# Tracks shorter than this are excluded from ALL stats (intros/outros are typically <90s)
MIN_REAL_SONG_SECONDS = 90

# Keyword markers for skits/interludes/intros — matched case-insensitively as whole tokens
import re as _re
_SKIT_KEYWORDS = ("intro", "outro", "interlude", "intermission", "skit", "prelude")
_SKIT_RE = _re.compile(
    r"(?:^|[\s\-\(\[\.,:])(" + "|".join(_SKIT_KEYWORDS) + r")(?:$|[\s\-\)\]\.,:])",
    _re.IGNORECASE,
)


def is_skit_or_interlude(track):
    """True if a track looks like a skit/intro/interlude/outro.

    Two signals:
      1. Title contains a marker word ("Intro", "Pablo Interlude", "Skit 4").
      2. Duration is suspiciously short (<MIN_REAL_SONG_SECONDS) — covers cases
         where the title is opaque (e.g. "Sssiiimmmrrr Surfffeeeer Intermission"
         that doesn't tokenize cleanly).
    """
    name = track.get("name") or ""
    if _SKIT_RE.search(name):
        return True
    duration_ms = track.get("duration_ms") or 0
    if 0 < duration_ms < MIN_REAL_SONG_SECONDS * 1000:
        return True
    return False


def build_artist_data(artist_name, force_refresh=False):
    """Resolve an artist + their albums + tracks + stream counts into a single dict."""
    print(f"\n[artist] Building data for: {artist_name}", flush=True)

    artist = spotify_client.search_artist(artist_name)
    if not artist:
        raise RuntimeError(f"Artist not found on Spotify: {artist_name}")

    print(f"[artist] Resolved -> {artist['name']} (id={artist['id']})", flush=True)
    artist_image = artist["images"][0]["url"] if artist.get("images") else None

    albums_meta = spotify_client.get_artist_albums(artist["id"])
    print(f"[artist] Loading tracks for {len(albums_meta)} albums...", flush=True)

    streams = kworb_scraper.get_stream_counts(artist["id"], force_refresh=force_refresh)
    print(f"[artist] Stream counts loaded for {len(streams)} tracks", flush=True)

    albums = []
    for a in albums_meta:
        tracks = spotify_client.get_album_tracks(a["id"])
        for t in tracks:
            t["streams"] = streams.get(t["id"])  # may be None for unranked tracks
        albums.append({**a, "tracks": tracks})

    return {
        "name": artist["name"],
        "spotify_id": artist["id"],
        "image": artist_image,
        "albums": albums,
    }


# ---------- Stat types ----------

def _albums_with_tracks(artist_data, require_streams=False, exclude_skits=True):
    """Filter to albums that have at least one usable real song.

    `exclude_skits` (default True) drops intros/outros/interludes by name+duration.
    Falls back to including all tracks if filtering would empty the album, so a
    cell never goes blank just because every track triggered the heuristic.
    """
    out = []
    for album in artist_data["albums"]:
        tracks = album["tracks"]
        if exclude_skits:
            non_skit = [t for t in tracks if not is_skit_or_interlude(t)]
            if non_skit:
                tracks = non_skit
        if require_streams:
            tracks = [t for t in tracks if t.get("streams") is not None]
        if tracks:
            out.append({**album, "_pool": tracks})
    return out


def _stat_min_streams(album):
    pool = [t for t in album["_pool"] if t["streams"] is not None]
    return min(pool, key=lambda t: t["streams"]) if pool else None


def _stat_max_streams(album):
    pool = [t for t in album["_pool"] if t["streams"] is not None]
    return max(pool, key=lambda t: t["streams"]) if pool else None


def _stat_longest(album):
    return max(album["_pool"], key=lambda t: t["duration_ms"])


def _stat_shortest(album):
    pool = [t for t in album["_pool"] if t["duration_ms"] >= MIN_SONG_SECONDS * 1000]
    if not pool:
        pool = album["_pool"]
    return min(pool, key=lambda t: t["duration_ms"])


def _format_duration(ms):
    total_seconds = ms // 1000
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def lowest_streamed_per_album(artist_data):
    items = []
    for album in _albums_with_tracks(artist_data, require_streams=True):
        track = _stat_min_streams(album)
        if not track:
            continue
        items.append({
            "album_name": album["name"],
            "album_image": album["image"],
            "value_text": kworb_scraper.format_streams(track["streams"]),
            "label_text": track["name"],
        })
    return {
        "title": f"{artist_data['name']} - Lowest Streamed Song on Each Album",
        "items": items,
    }


def highest_streamed_per_album(artist_data):
    items = []
    for album in _albums_with_tracks(artist_data, require_streams=True):
        track = _stat_max_streams(album)
        if not track:
            continue
        items.append({
            "album_name": album["name"],
            "album_image": album["image"],
            "value_text": kworb_scraper.format_streams(track["streams"]),
            "label_text": track["name"],
        })
    return {
        "title": f"{artist_data['name']} - Most Streamed Song on Each Album",
        "items": items,
    }


def longest_song_per_album(artist_data):
    items = []
    for album in _albums_with_tracks(artist_data):
        track = _stat_longest(album)
        items.append({
            "album_name": album["name"],
            "album_image": album["image"],
            "value_text": _format_duration(track["duration_ms"]),
            "label_text": track["name"],
        })
    return {
        "title": f"{artist_data['name']} - Longest Song on Each Album",
        "items": items,
    }


def shortest_song_per_album(artist_data):
    items = []
    for album in _albums_with_tracks(artist_data):
        track = _stat_shortest(album)
        items.append({
            "album_name": album["name"],
            "album_image": album["image"],
            "value_text": _format_duration(track["duration_ms"]),
            "label_text": track["name"],
        })
    return {
        "title": f"{artist_data['name']} - Shortest Song on Each Album",
        "items": items,
    }


# Registry of all available stat types. Order = preferred posting order.
STAT_TYPES = {
    "lowest_streamed_per_album":  lowest_streamed_per_album,
    "highest_streamed_per_album": highest_streamed_per_album,
    "longest_song_per_album":     longest_song_per_album,
    "shortest_song_per_album":    shortest_song_per_album,
}


def build_stat(artist_data, stat_type):
    """Build the grid data for a given stat type."""
    if stat_type not in STAT_TYPES:
        raise ValueError(f"Unknown stat type: {stat_type}. Known: {list(STAT_TYPES)}")
    return STAT_TYPES[stat_type](artist_data)


if __name__ == "__main__":
    import sys
    name = " ".join(sys.argv[1:]) or "Kanye West"
    data = build_artist_data(name)
    for stat_type in STAT_TYPES:
        print(f"\n=== {stat_type} ===")
        result = build_stat(data, stat_type)
        print(f"Title: {result['title']}")
        for item in result["items"]:
            print(f"  {item['value_text']:>8}  {item['album_name'][:30]:30s}  {item['label_text']}")
