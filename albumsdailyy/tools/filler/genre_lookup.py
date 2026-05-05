"""
Map an album-review artist to a "popular artist in the same genre" pulled
from albumsdailyy/tools/filler/genre_artists.yaml.

The Spotify API exposes `artist.genres` as a flat list of subgenre strings, e.g.
    ["chicago rap", "hip hop", "rap", "pop rap", "conscious hip hop"]
or
    ["dance pop", "pop", "post-teen pop"]

We collapse that into one of our coarse buckets (`hip-hop`, `r&b`, `pop`, ...).
"""

import os
import random
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Tracked config — lives next to the filler module so it gets committed.
YAML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genre_artists.yaml")


# Substrings to look for in the artist's Spotify genres.
# Order matters: first hit wins, so put more specific buckets above generic ones.
_GENRE_RULES = [
    ("hip-hop",   ("hip hop", "rap", "trap", "drill")),
    ("r&b",       ("r&b", "rnb", "neo soul", "soul")),
    ("country",   ("country",)),
    ("electronic",("electronic", "edm", "house", "dubstep", "techno", "trance", "drum and bass")),
    ("indie",     ("indie", "bedroom pop", "lo-fi")),
    ("rock",      ("rock", "alternative", "punk", "metal", "grunge")),
    ("pop",       ("pop",)),  # generic — keep last
]


def _yaml_load(path):
    """Tiny YAML reader for our flat schema (no nesting except lists).

    Avoids adding PyYAML as a hard dep — the file is simple enough.
    """
    if not os.path.exists(path):
        return {}
    data = {}
    current_key = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # "- value" item under a list key
            if stripped.startswith("- ") and current_key is not None:
                data[current_key].append(stripped[2:].strip())
                continue
            # "key: value" or "key:" (start a list)
            m = re.match(r"^([A-Za-z0-9_&\-]+)\s*:\s*(.*)$", stripped)
            if m:
                key = m.group(1).strip().lower()
                value = m.group(2).strip()
                if value == "":
                    data[key] = []
                    current_key = key
                else:
                    data[key] = value
                    current_key = None
    return data


def load_genre_artists(path=YAML_PATH):
    """Returns the parsed YAML dict — keys are genres, values are lists of artists."""
    return _yaml_load(path)


def detect_bucket(spotify_genres):
    """Map a list of Spotify genres to one of our coarse buckets.
    Returns None if nothing matches (caller should use default).
    """
    if not spotify_genres:
        return None
    joined = " | ".join(g.lower() for g in spotify_genres)
    for bucket, needles in _GENRE_RULES:
        if any(n in joined for n in needles):
            return bucket
    return None


def pick_filler_artist(spotify_genres, exclude=(), seed=None):
    """Return a popular artist for the filler post given an album-review artist's genres.

    `exclude` is a list of artist names to skip (typically: the album-review artist
    itself, plus anything we've used recently). Falls back to the YAML's `default`
    bucket if nothing matches the artist's genres.
    """
    catalog = load_genre_artists()
    if not catalog:
        return None, None

    bucket = detect_bucket(spotify_genres) or catalog.get("default", "hip-hop")
    artists = catalog.get(bucket, [])
    excluded_lower = {a.strip().lower() for a in exclude}
    pool = [a for a in artists if a.strip().lower() not in excluded_lower]

    if not pool:
        # Bucket exhausted by exclude list — try the default bucket
        bucket = catalog.get("default", "hip-hop")
        artists = catalog.get(bucket, [])
        pool = [a for a in artists if a.strip().lower() not in excluded_lower]

    if not pool:
        return None, bucket

    rng = random.Random(seed) if seed is not None else random
    return rng.choice(pool), bucket


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(PROJECT_ROOT))
    from albumsdailyy.tools.artist_stats.spotify_client import search_artist
    name = " ".join(sys.argv[1:]) or "Kanye West"
    artist = search_artist(name)
    if not artist:
        print(f"Not found: {name}"); sys.exit(1)
    print(f"Spotify genres for {artist['name']}: {artist.get('genres')}")
    bucket = detect_bucket(artist.get("genres", []))
    print(f"Bucket: {bucket}")
    pick, used_bucket = pick_filler_artist(artist.get("genres", []), exclude=[artist["name"]])
    print(f"Filler artist: {pick}  (from bucket: {used_bucket})")
