# Generate Short Instagram Reel (Top 3 / Bottom 3)

## Objective
Create a 20-30 second 9:16 Instagram reel showing the **3 worst and 3 best songs** from an album ranking, with a hook-based opening designed to stop the scroll.

## Required Input
- Path to a markdown file with album rankings (same format as full reel)
- Album should have 7+ songs for best results (otherwise all songs are used)

## Pre-flight Checks
Before running the pipeline, verify:
1. `yt-dlp --version` — must be installed
2. `ffmpeg -version` — must be installed
3. `python -c "import moviepy"` — must be installed
4. Create directories if needed: `.tmp/audio/`, `.tmp/broll/`, `.tmp/output/`

## Pipeline Steps

### Step 1: Parse Markdown
```
python tools/parse_markdown.py <input_file.md>
```
- Verify `.tmp/album_data.json` was created
- Check song count and ratings look correct
- Songs will be sorted worst-to-best automatically

### Step 2: Download Audio
```
python tools/download_audio.py
```
- Downloads ALL songs (we need both extremes for bottom 3 / top 3)
- Check `.tmp/audio/manifest.json` for any `null` entries
- Critical: ensure the top 3 and bottom 3 songs downloaded successfully

### Step 3: Download B-Roll
```
python tools/download_broll.py
```
- Downloads all clips (same as full reel)
- Check `.tmp/broll/manifest.json`
- Verify clips exist for at least the 6 selected songs

### Step 4: Calculate Short Timing
```
python tools/short_reel/calculate_short_timing.py
```
- Review `.tmp/short_timing.json`
- 6 songs selected: bottom 3 + top 3
- Bottom 3 get 2s each, top 3 get 3-4s each (escalating)
- Body duration should be ~16.5s
- With hook card (~2.5s) + end card (~4s) - crossfades = ~20-25s total

### Step 5: Compose Short Reel
```
python tools/short_reel/compose_short_reel.py
```
- Verify output exists at `.tmp/output/short_reel_final.mp4`
- Check total duration is 20-30 seconds
- Check file is > 1MB and < 100MB
- Output is 1080x1920 at 30fps
- Add `--draft` flag for fast test renders

## Post-flight
- Report output path, total duration, and file size to user
- Short reels should typically be 2-8 MB

## Reel Structure

### Hook Card (~2.5s)
Dynamic hook text auto-generated based on album data:
- Low worst rating (<=4): **"ONE SONG GOT A {rating}/10..."** — shock value
- Perfect best rating (10): **"IS THIS A PERFECT ALBUM?"** — intrigue
- Large rating spread (>=6): **"FROM {min}/10 TO {max}/10"** — drama
- Fallback: **"MY TOP 3 AND BOTTOM 3"**

Background: dimmed B-Roll. Album cover centered. Album/artist name. "WORST TO BEST" label.

### Body (6 songs)
- **Bottom 3** (~2s each): Quick flashes, shock value from low ratings
- **Top 3** (3-4s each, escalating): Building anticipation, best song gets most time
- Same overlays as full reel: countdown number (top-left), rating (top-right), song name (center)
- Countdown numbers use original album positions (e.g., #14, #13, #12... #3, #2, #1)

### End Card (~4s)
Exact same end card as the full reel — album title, cover + average rating + color legend, full song list.

## When to Use This vs Full Reel
- Use **Short Reel** for: teasers, higher engagement, albums with 10+ songs, quick content
- Use **Full Reel** (`workflows/generate_full_reel.md`) for: deep dives, smaller albums, comprehensive coverage
- Can generate **BOTH** from the same album data — Steps 1-3 are shared

## Testing Hook Card
To test the hook card in isolation:
```
python tools/short_reel/test_hookcard.py
```
Output: `.tmp/output/hookcard_test.mp4`

## Known Issues / Lessons Learned
_(Updated as issues are encountered)_
