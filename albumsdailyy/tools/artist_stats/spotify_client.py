"""
Spotify Web API client (Client Credentials flow — read-only metadata).

Provides:
  - search_artist(name) -> {id, name, image, ...}
  - get_artist_albums(artist_id) -> [{id, name, release_year, image, ...}, ...]
  - get_album_tracks(album_id) -> [{id, name, duration_ms, track_number}, ...]
  - get_album(album_id) -> full album object incl. tracks

Reads credentials from project-root .env:
  SPOTIFY_CLIENT_ID
  SPOTIFY_CLIENT_SECRET

Token is cached in-memory for the process lifetime.
"""

import base64
import json
import os
import time
import urllib.parse
import urllib.request

# project root: .../Music Reel
_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

_TOKEN_CACHE = {"value": None, "expires_at": 0.0}


def _load_env():
    env = {}
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def _get_token():
    """Get a Spotify access token via Client Credentials flow. Cached for ~55min."""
    now = time.time()
    if _TOKEN_CACHE["value"] and _TOKEN_CACHE["expires_at"] > now + 30:
        return _TOKEN_CACHE["value"]

    env = _load_env()
    cid = env.get("SPOTIFY_CLIENT_ID")
    secret = env.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "Spotify credentials missing. Add SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET to .env (register an app at "
            "developer.spotify.com/dashboard)."
        )

    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    print("  [spotify] requesting access token...", flush=True)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    _TOKEN_CACHE["value"] = data["access_token"]
    _TOKEN_CACHE["expires_at"] = now + int(data.get("expires_in", 3600))
    print(f"  [spotify] token ok (expires in {data.get('expires_in')}s)", flush=True)
    return _TOKEN_CACHE["value"]


def _api(path, params=None, retries=3):
    """GET https://api.spotify.com/v1{path} with auth. Retries on 429/5xx."""
    token = _get_token()
    url = f"https://api.spotify.com/v1{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "2"))
                print(f"  [spotify] 429 rate-limited, sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                time.sleep(1 + attempt)
                continue
            # Token might have expired mid-request (rare with our 55min cache)
            if e.code == 401 and attempt == 0:
                _TOKEN_CACHE["value"] = None
                token = _get_token()
                continue
            # Surface the response body so 4xx errors are debuggable
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise RuntimeError(f"Spotify API {e.code} for {url}: {body[:300]}") from e
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"Spotify API failed for {path}: {last_err}")


# ---------- Public API ----------

def search_artist(name):
    """Find an artist by name. Returns the top match dict or None."""
    print(f"  [spotify] search artist: {name}", flush=True)
    data = _api("/search", {"q": name, "type": "artist", "limit": 5})
    items = data.get("artists", {}).get("items", [])
    if not items:
        return None

    # Prefer exact (case-insensitive) name match if available
    name_lower = name.strip().lower()
    for it in items:
        if it["name"].strip().lower() == name_lower:
            return it
    return items[0]


def get_artist_albums(artist_id, include_groups=("album",), market="US"):
    """List an artist's albums, deduping by name (keeps earliest release).

    include_groups: tuple of any of 'album', 'single', 'compilation', 'appears_on'.
    """
    print(f"  [spotify] albums for artist {artist_id}", flush=True)
    items = []
    offset = 0
    # Spotify enforces limit<=10 on this endpoint as of late 2025
    page_size = 10
    while True:
        page = _api(f"/artists/{artist_id}/albums", {
            "include_groups": ",".join(include_groups),
            "market": market,
            "limit": page_size,
            "offset": offset,
        })
        items.extend(page.get("items", []))
        if page.get("next") is None:
            break
        offset += page_size

    # Dedupe by normalized name, keep the earliest release.
    # Strip common edition suffixes so "Album" / "Album (Deluxe)" / "Album (Remastered)"
    # all collapse to the same key.
    import re as _re
    _EDITION_WORDS = (
        r"deluxe|remaster(ed)?|edition|expanded|anniversary|edited|explicit|clean|"
        r"bonus|live|special|version|director'?s cut|reissue|extended|reloaded|"
        r"collector'?s?(\s+edition)?|remix(es)?|tour|instrumentals?|uncut|complete|"
        r"ultra|platinum|gold|silver|standard|original|international|japan(ese)?|"
        r"acoustic|acapella|stripped|unplugged|demo|cd"
    )
    _suffix_re = _re.compile(
        r"\s*[\(\[][^()\[\]]*(" + _EDITION_WORDS + r")[^()\[\]]*[\)\]]\s*",
        _re.IGNORECASE,
    )
    # Trailing edition phrase: " - Deluxe", ": Remastered", or just "  Collectors Edition"
    # after the main title (e.g. "DAMN. COLLECTORS EDITION.").
    _trailing_re = _re.compile(
        r"(?:\s*[-:–—]\s*|\s+)(" + _EDITION_WORDS + r")\b.*$",
        _re.IGNORECASE,
    )
    def _norm(name):
        # Iterate suffix removal so names like "Album (Deluxe) (Remastered)" collapse
        prev = None
        n = name.strip()
        while prev != n:
            prev = n
            n = _suffix_re.sub(" ", n).strip()
            n = _trailing_re.sub("", n).strip()
        return _re.sub(r"\s+", " ", n).lower()

    by_name = {}
    for a in items:
        key = _norm(a["name"])
        existing = by_name.get(key)
        if existing is None or a["release_date"] < existing["release_date"]:
            by_name[key] = a

    albums = []
    for a in by_name.values():
        albums.append({
            "id": a["id"],
            "name": a["name"],
            "release_date": a["release_date"],
            "release_year": a["release_date"][:4],
            "total_tracks": a["total_tracks"],
            "image": a["images"][0]["url"] if a.get("images") else None,
            "album_type": a.get("album_type"),
        })
    albums.sort(key=lambda x: x["release_date"])
    print(f"  [spotify] {len(albums)} albums (after dedupe)", flush=True)
    return albums


def get_album(album_id, market="US"):
    """Full album details including tracks. Returns Spotify album object."""
    return _api(f"/albums/{album_id}", {"market": market})


def get_album_tracks(album_id, market="US"):
    """List tracks on an album. Returns [{id, name, duration_ms, track_number}, ...]."""
    print(f"  [spotify] tracks for album {album_id}", flush=True)
    items = []
    offset = 0
    while True:
        page = _api(f"/albums/{album_id}/tracks", {
            "market": market, "limit": 50, "offset": offset,
        })
        items.extend(page.get("items", []))
        if page.get("next") is None:
            break
        offset += 50
    return [{
        "id": t["id"],
        "name": t["name"],
        "duration_ms": t["duration_ms"],
        "track_number": t["track_number"],
        "disc_number": t.get("disc_number", 1),
    } for t in items]


if __name__ == "__main__":
    import sys
    name = " ".join(sys.argv[1:]) or "Kanye West"
    artist = search_artist(name)
    if not artist:
        print(f"No artist found for '{name}'")
        sys.exit(1)
    print(f"\nArtist: {artist['name']} (id={artist['id']})")
    albums = get_artist_albums(artist["id"])
    print(f"\n{len(albums)} albums:")
    for a in albums:
        print(f"  {a['release_year']}  {a['name']}  ({a['total_tracks']} tracks)")
