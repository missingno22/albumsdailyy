# Generate Static Instagram Post (2-Image Carousel)

## Objective
Create a 2-image Instagram carousel post (1080x1350, 4:5 portrait) from an album ranking: a title card and a ratings card.

## Required Input
- Path to a markdown file with album rankings (same format as reels)

## Pre-flight Checks
Before running the pipeline, verify:
1. `python -c "from PIL import Image"` — Pillow must be installed
2. Create directories if needed: `.tmp/broll/`, `.tmp/output/`
3. No ffmpeg or moviepy required (static images only)

## Pipeline Steps

### Step 1: Parse Markdown
```
python tools/parse_markdown.py <input_file.md>
```
- Verify `.tmp/album_data.json` was created
- Check song count and ratings look correct

### Step 2: Download Album Cover
```
python tools/download_broll.py
```
- Only the album cover is needed; B-Roll video clips are downloaded but not used
- Check `.tmp/broll/manifest.json` has a non-null `album_cover` field
- If cover download fails, you can manually place an image at `.tmp/broll/album_cover.jpg`

### Step 3: Compose Static Post
```
python tools/static_post/compose_static_post.py
```
- Outputs: `.tmp/output/post_slide_1.png` and `.tmp/output/post_slide_2.png`
- Both images are 1080x1350 pixels (4:5 Instagram portrait)
- Generation takes <5 seconds (no video rendering)

## Post-flight
- Report both output file paths and file sizes
- Both PNGs should be well under 5 MB (Instagram limit)
- Preview both images before uploading

## Post Structure

### Slide 1: Title Card
- Background: blurred album cover with dark overlay
- Album number top-left (e.g. "1/1000")
- "EVERY SONG RATED" hook text in gold
- Album cover centered with white border
- Album name, artist name, and song count

### Slide 2: Ratings Card
- Background: blurred album cover with dark overlay (consistent look)
- Album title centered at top
- Left: album cover | Right: average rating (color-coded) + color legend
- Bottom: full song list with rank, name, and rating — all color-coded

## When to Use This vs Reels
- Use **Static Post** for: carousel content, quick generation, no audio needed, swipe interaction
- Use **Full Reel** (`workflows/generate_full_reel.md`) for: deep dives, comprehensive video coverage
- Use **Short Reel** (`workflows/generate_short_reel.md`) for: teasers, hook-based video content
- Can generate ALL formats from the same album data — Steps 1-2 are shared

## Testing
To quickly test the output:
```
python tools/static_post/test_static_post.py
```
Output: `.tmp/output/post_slide_1.png` and `.tmp/output/post_slide_2.png`

## Known Issues / Lessons Learned
_(Updated as issues are encountered)_
