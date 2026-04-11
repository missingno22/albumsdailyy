# Generate Full-Length Instagram Reel from Album Ranking

## Objective
Create a 9:16 Instagram reel that counts down **every song** from worst to best, with B-Roll video and audio from each song. Total duration: ~60-90 seconds.

## Required Input
- Path to a markdown file with album rankings in this format:
```
# Album Title
## Artist Name
1) Song Name - 8/10
2) Another Song - 7/10
```

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
- Check `.tmp/audio/manifest.json` for any `null` entries (failed downloads)
- If top 5 songs failed to download, retry with different search terms or ask user for YouTube URLs
- This step can take several minutes depending on song count

### Step 3: Download B-Roll
```
python tools/download_broll.py
```
- Check `.tmp/broll/manifest.json`
- Verify at least 3 clips were downloaded
- If too few clips, the compose step will cycle through what's available

### Step 4: Calculate Timing
```
python tools/full_reel/calculate_full_timing.py
```
- Review `.tmp/timing.json` — total duration should be 30-90 seconds
- Bottom songs get ~5s each, top 5 get 5-8s each
- If user wants a specific duration, the script can be adjusted

### Step 5: Compose Reel
```
python tools/full_reel/compose_full_reel.py
```
- This is the longest step (rendering video)
- Verify output exists at `.tmp/output/reel_final.mp4`
- Check file is > 1MB and < 100MB (Instagram limit)
- Output is 1080x1920 at 30fps
- Add `--draft` flag for fast test renders

## Post-flight
- Report output path, total duration, and file size to user
- If file is too large for Instagram (>100MB), re-render with lower bitrate

## Text Overlay Layout
- **Top-left**: Countdown number (e.g. "#7") — large bold white text with black outline
- **Top-right**: Rating (e.g. "8/10") — bold white text with black outline
- **Center**: Song name — bold white text with black outline, font size scales with name length
- No background panels — text sits directly on B-Roll

## Reel Structure
1. **Title card** (~3s): "EVERY SONG RATED" hook, album cover, artist name, song count
2. **Song segments** (all songs, worst to best): B-Roll + audio + countdown/rating/name overlays
3. **End card** (~6s): Album title, cover + average rating + color legend, full song list

## When to Use This vs Short Reel
- Use **Full Reel** for: deep dives, smaller albums, audience that wants comprehensive coverage
- Use **Short Reel** (`workflows/generate_short_reel.md`) for: teasers, higher engagement, quick content

## Known Issues / Lessons Learned
- **MoviePy renders may fail Instagram processing**: The default MoviePy H.264 output can produce files that Instagram's server rejects with a generic "ERROR" status. Fix: re-encode with ffmpeg before uploading:
  ```bash
  ffmpeg -y -i .tmp/output/reel_final.mp4 -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart -r 30 .tmp/output/reel_final_compat.mp4
  ```
  The `-movflags +faststart` flag is critical — it moves the moov atom to the beginning of the file so Instagram can start processing immediately. This also reduces file size significantly (~35% smaller).
