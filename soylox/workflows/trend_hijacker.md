# Trend-Repost Content Engine

## Objective
Detect rising trends (TikTok Creative Center + Google Trends), find the actual viral videos driving each trend, and repost them with light repackaging (9:16 reformat, ~3% speed nudge, audio loudnorm) to slip past Instagram's duplicate-content detection. 4–6 reels/day, scheduled across a rolling 12-hour window.

**This is a reposter, not a generator.** We ride waves — we don't invent them.

## Pipeline

```
detect_trends → fetch_trend_videos → repackage_video → generate_caption → Flask queue
```

Each stage is a standalone Python tool in `soylox/tools/`. Orchestrator: `fill_queue.py`.

## Required Setup
- `.env` has `OPENAI_API_KEY` set
- `ffmpeg` + `ffprobe` on PATH
- `yt-dlp` installed (pip)
- `playwright install chromium` run once

## Pipeline Steps

### Step 1: Detect trends
```bash
python soylox/tools/detect_trends.py --limit 10
```
- Playwright scrapes TikTok Creative Center hashtags (velocity, post counts)
- Pulls Google Trends RSS (daily trending searches with approx traffic)
- Dedups against `soylox/inputs/archive.txt`
- Writes `soylox/.tmp/trends_YYYYMMDD_HHMM.json`

### Step 2: Fetch actual trend videos
```bash
python soylox/tools/fetch_trend_videos.py --trend <trends.json> --index 0 --max-videos 2
```
For each trend:
1. Scrape Creative Center hashtag detail page for top posts (Playwright)
2. Scrape tiktok.com/search?q=#tag (Playwright)
3. yt-dlp each TikTok video URL individually (more reliable than tag pages)
4. Last resort: YouTube Shorts search

Output: `.tmp/trends/<slug>/clips/NN.mp4` + `.info.json` metadata.

### Step 3: Repackage (light variation)
```bash
python soylox/tools/repackage_video.py --in src.mp4 --out packaged.mp4 --speed 0.97
```
- 9:16 reformat: blurred letterbox (landscape source) or center-crop (taller source) or scale (already vertical)
- Speed nudge: randomly picked from 0.95–1.05 per reel (defeats audio fingerprint)
- Audio loudnorm: -14 LUFS (IG-friendly)
- `+faststart` H.264 MP4

### Step 4: Generate IG caption
```bash
python soylox/tools/generate_caption.py --trend <trends.json> --index 0 \
    --meta '{"uploader":"...","title":"..."}'
```
GPT-4o-mini single shot (~$0.0002/video). Returns:
```json
{"caption": "...", "hashtags": ["#fyp", "#viral", ...]}
```

System prompt ensures captions reference the trend by name (e.g., "the 67 meme") rather than pretending originality.

### Step 5: Full run + queue
```bash
python soylox/tools/fill_queue.py --count 4 --json
```
- Overfetches 3x the trend pool (many trends yield 0 downloadable videos)
- Emits one JSON line per completed reel: `{title, video_path, caption, scheduled_datetime}`
- Flask captures those lines and inserts them as pending queue entries

## Scheduling Policy
- **Horizon**: next 12h (trends decay fast; don't push posts out days)
- **Gap**: 90 min minimum between posts
- **Start offset**: +15 min from now (buffer for user review)

## Flask Integration
Automation script command (no change needed from previous setup):
```
python soylox/tools/fill_queue.py --json --count 4
```

## Known Issues / Lessons Learned

- **yt-dlp on `tiktok.com/tag/<tag>`** commonly errors with "No working app info is available". Our fetcher uses Playwright to harvest individual `/video/<id>` URLs first, then yt-dlp those — way more reliable.
- **TikTok Creative Center DOM shifts** frequently. If detect_trends returns 0 hashtags, inspect the live DOM with `headless=False` and update the anchor/card selectors.
- **Google Trends pytrends API is broken** (Apr 2025+). We use the official RSS feed at `trends.google.com/trending/rss?geo=US` instead — stable.
- **TikTok search page** heavily rate-limits / uses fingerprinting. If repeated scrapes fail, space them out or rotate user-agents.
- **Archive bloat**: `inputs/archive.txt` grows forever. Truncate to last 500 lines every few weeks to avoid false-positive dedup on returning trends.
- **Some trends are unrepostable** (news events, brand campaigns). The pipeline will skip any trend that yields 0 downloadable videos and move to the next.

## Monitoring
- Sample 1 reel/day before approving: verify the trend is recognizable and the repackage didn't mangle the video
- Track engagement after 2 weeks; compare speed variants (0.95 vs 1.05) for algorithmic differences
- If IG flags a reel as duplicate, bump the speed range or add mirror flip to `repackage_video.py`

## Future Upgrades (deferred)
- Sound-based trend detection (TikTok trending sounds, not just hashtags)
- Mirror flip as optional repackaging variant
- Feedback loop: IG insights → adjust velocity scoring per trend category
- Subtle watermark/logo burn if we want to brand content
