# Workflow: Create & Schedule Artist Stats Posts

## Objective
After every album review, post an "Artist Stats" reel + image post the next day,
using a stat type that has not been used for that artist before.

## Posting cadence

```
Day 1  19:00   album_review (reel)        — by artist X
Day 2  14:00   stats_reel    (reel)       ┐  same artist X, same stat_type
Day 2  20:00   stats_post    (image)      ┘
Day 3  19:00   album_review (reel)        — by artist Y
Day 4  14:00   stats_reel                 ┐  artist Y, fresh stat_type
Day 4  20:00   stats_post                 ┘
...
```

If artist X has already used every stat type in `STAT_TYPES`, **the stats day
falls back to another album review** (Day 2 above becomes a normal album_review).

## Required inputs
- `inputs/*.md` — album markdowns (existing format).
- `.env` keys:
  - `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` (Client Credentials flow,
    register an app at developer.spotify.com/dashboard)

## Stat types
Defined in `tools/artist_stats/stats_registry.py`:

| Key | Description | Data source |
|---|---|---|
| `lowest_streamed_per_album`  | Least-streamed track per album | Kworb |
| `highest_streamed_per_album` | Most-streamed track per album  | Kworb |
| `longest_song_per_album`     | Longest track per album        | Spotify only |
| `shortest_song_per_album`    | Shortest track per album (skits >60s excluded) | Spotify only |

Stream counts come from `kworb.net/spotify/artist/{spotify_id}_songs.html`
(cached 24h in `.tmp/kworb_cache/`). Spotify has no streams in their API.

## How to run

### 1. Generate a single stats post or reel manually (testing)
```
python -m albumsdailyy.tools.artist_stats.generate_stats_post "Kanye West" lowest_streamed_per_album
python -m albumsdailyy.tools.artist_stats.generate_stats_reel "Kanye West" lowest_streamed_per_album --draft
```

Outputs:
- Image: `outputs/stats/<Artist>_<stat_type>.png` (1080x1350)
- Reel:  `outputs/stats_reels/<Artist>_<stat_type>.mp4` (1080x1920, ~12s)

### 2. Fill the posting queue (automated)
```
python -m albumsdailyy.tools.fill_queue --days 14
python -m albumsdailyy.tools.fill_queue --days 14 --json   # for the Flask scheduler
```

The fill loop:
1. Reads existing queue from `outputs/queue.db`.
2. Walks forward day-by-day from tomorrow:
   - If last queued entry is `album_review` → today is `stats_day`.
   - Else → today is `album_review`.
3. For `stats_day`:
   - Artist = artist of the most recent `album_review`.
   - Pick first stat type from `STAT_TYPES` order that isn't recorded in
     `posted_artist_stats` and isn't already in the queue.
   - If none → fall back to `album_review` for that day.
   - Render reel + image (cached on disk; `--draft` for fast iteration).
   - Insert two queue rows: stats_reel @ 14:00, stats_post @ 20:00.
   - Record (artist, stat_type) in `posted_artist_stats`.

## DB schema additions

### `queue` (modified)
- `scheduled_time TEXT NOT NULL DEFAULT '19:00'` — HH:MM, sub-day ordering
- `post_type TEXT NOT NULL DEFAULT 'album_review'` — `album_review` | `stats_reel` | `stats_post`
- `post_format TEXT NOT NULL DEFAULT 'reel'` — `reel` | `image`
- `stat_type TEXT` — nullable, set for stats_* rows
- The `UNIQUE` constraint on `scheduled_date` was dropped (multi-post days).

### `posted_artist_stats` (new)
Tracks which (artist, stat_type) combos have been used. Lookup is
case-insensitive on artist name. `UNIQUE(artist_key, stat_type)`.

## Open follow-ups for the Instagram side

The Flask `data/queue.db` and n8n webhook still treat every entry as a video
upload. For `post_format='image'` rows, the n8n flow needs to call the
Instagram **image** media endpoint instead of the reel endpoint:

- Reel:  `POST /{ig_user_id}/media` with `media_type=REELS&video_url=<mp4>`
- Image: `POST /{ig_user_id}/media` with `image_url=<png>` (no media_type)

The Flask app sends `video_url` to n8n today; for stats_post rows the file
served to catbox is a `.png`. Update n8n to inspect the file extension or
`post_format` to choose the correct endpoint.

## Failure handling

| Symptom | Cause | Fix |
|---|---|---|
| `Spotify credentials missing` | `.env` keys absent | Add `SPOTIFY_CLIENT_ID`/`SECRET`. |
| `Artist not found on Spotify` | Search miss | Use exact artist name in markdown. |
| `kworb returned HTTP 4xx` | Wrong artist id / kworb unavailable | Check `https://kworb.net/spotify/artist/{id}_songs.html` in browser. Cached html lives in `.tmp/kworb_cache/`. |
| All stat types used | Expected for prolific catalog | Add new stat types to `stats_registry.STAT_TYPES`. |
| Stats post has empty grid | Album metadata missing OR all stream counts unavailable | Check `kworb` page; some artists have weak coverage. |

## Files

- `tools/artist_stats/spotify_client.py` — Spotify Web API wrapper
- `tools/artist_stats/kworb_scraper.py` — kworb stream-count scraper
- `tools/artist_stats/stats_registry.py` — stat-type definitions + `build_artist_data`
- `tools/artist_stats/generate_stats_post.py` — 1080x1350 PNG renderer
- `tools/artist_stats/generate_stats_reel.py` — 1080x1920 MP4 renderer
- `tools/flask_app/models.py` — DB schema + dedupe helpers
- `tools/fill_queue.py` — alternating schedule planner
