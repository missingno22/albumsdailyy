# Workflow: Create Filler / Shitpost Reel

## Objective
After every album-review + stats-day cycle, post one filler reel — a TikTok meme
clip of a popular artist in the **same genre** as the album-review artist
(usually hip-hop). Reposted with light variation to slip past IG's duplicate
detection.

This is the trend-hijack equivalent of soylox, scoped to a single artist per run.

## Where it sits in the schedule

```
Day 1   19:00   album_review            (Artist X — your album of the day)
Day 2   14:00   stats_reel              (Artist X)
Day 2   20:00   stats_post              (Artist X — image)
Day 3   19:00   FILLER                  (Popular artist in X's genre)
Day 4   19:00   album_review            (Artist Y — next album)
...
```

3-day cycle. Filler day uses one slot at 19:00.

## Pipeline

```
seed_artist (X)
   │
   ├─ search_artist(X)                       — Spotify, get artist.genres
   ├─ detect_bucket(genres)                  — collapse to hip-hop / r&b / pop / ...
   ├─ pick_filler_artist(bucket, exclude)    — random from tools/filler/genre_artists.yaml
   │
   ├─ fetch_meme_clip(filler_artist)         — soylox TikTok+Reddit scrape
   ├─ repackage_video.py                     — 9:16 + speed nudge + loudnorm
   ├─ generate_caption.py                    — OpenAI brainrot caption
   │
   └─ insert into queue.db (post_type='filler', artist=filler_artist)
```

## Required setup

- `.env` keys (reused from soylox):
  - `OPENAI_API_KEY` (caption generation)
- Tools:
  - `yt-dlp` on PATH
  - `ffmpeg` + `ffprobe` on PATH
  - `playwright install chromium` run once
- Files:
  - `albumsdailyy/tools/filler/genre_artists.yaml` — curated artist list per genre

## How to run

### Generate one filler manually (testing)
```
python -m albumsdailyy.tools.filler.generate_filler_post "Kanye West"
python -m albumsdailyy.tools.filler.generate_filler_post "Kanye West" --filler-artist "Travis Scott"
```

Output:
- `outputs/filler/<seed>__<filler>.mp4` — 9:16 reel-ready video
- `outputs/filler/<seed>__<filler>.json` — sidecar with caption + source URL

### Fill the queue end-to-end
```
python -m albumsdailyy.tools.fill_queue --days 14
```

The planner walks forward day-by-day. On each filler day:
- Seed artist = the most recent `album_review` artist.
- Genre bucket detected from Spotify's `artist.genres`.
- Filler artist picked at random from the YAML, excluding the last 5 used
  (keeps variety).
- TikTok scrape, light repackage, caption.
- Inserted into the queue with `post_type='filler'`.

If any step fails (no clip found, OpenAI down, etc.), the day **falls back to
an album_review** so the queue still advances.

## Editing the artist roster

Edit `albumsdailyy/tools/filler/genre_artists.yaml` directly. Keys:

```yaml
hip-hop:        # bucket name (referenced in genre_lookup.py)
  - Drake       # any number of artists; longer list = more variety
  - Travis Scott
  ...

default: hip-hop  # fallback bucket when artist genres don't match
```

To add a new bucket, also add a row to `_GENRE_RULES` in
`albumsdailyy/tools/filler/genre_lookup.py` mapping Spotify genre substrings to
the new bucket name.

## DB

No schema change. Filler posts use the existing `queue` table with:

| column        | value                                         |
|---------------|-----------------------------------------------|
| `post_type`   | `filler`                                      |
| `post_format` | `reel`                                        |
| `artist`      | the **filler** artist (the popular one)       |
| `album_name`  | `<filler> (filler / <seed>)`                  |
| `album_slug`  | `filler-<filler-artist-slug>`                 |
| `stat_type`   | NULL                                          |

## Failure handling

| Symptom | Likely cause | Fix |
|---|---|---|
| `Could not find a meme clip` | TikTok blocked Playwright; Reddit search empty | Re-run; if persistent, try a different filler artist or update soylox/fetch_trend_videos.py selectors |
| `Caption gen failed → fallback used` | `OPENAI_API_KEY` missing / rate limit | Add key; the fallback caption is fine for one-offs |
| Filler artist picked = album review artist | Override would be confusing | The picker auto-excludes the seed artist + last 5 fillers |
| All artists in bucket excluded | Tiny bucket (e.g. country with 6 names, all recently used) | Add more entries to the YAML |

## Files

- `tools/filler/genre_lookup.py` — YAML loader + Spotify-genre → bucket mapper
- `tools/filler/fetch_artist_meme.py` — wraps soylox scrapers for artist queries
- `tools/filler/generate_filler_post.py` — end-to-end orchestrator
- `tools/filler/genre_artists.yaml` — curated artists per genre
- `tools/fill_queue.py` — 3-day cycle planner

## Reused from soylox

- `soylox/tools/fetch_trend_videos.py` (TikTok Creative Center, TikTok web search,
  Reddit fallback, yt-dlp wrapper)
- `soylox/tools/repackage_video.py` (9:16 reformat, speed nudge, loudnorm)
- `soylox/tools/generate_caption.py` (OpenAI brainrot caption generation)
