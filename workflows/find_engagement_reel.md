# Find and Download Engagement Reel

## Objective
Find viral, high-engagement Instagram reels about an artist (memes, edits, fan content) and download them locally. These reels can be reposted, shared to stories, or used as inspiration for original content.

## Required Input
- Path to an album markdown file (e.g. `albums/sample_album.md`) **or** an artist name
- The markdown file must have the artist on line 2: `## Artist Name`

## Prerequisites
- `yt-dlp` installed (`yt-dlp --version`)
- `requests` installed (`pip install requests`)
- Firefox with an active Instagram login session (the tool reads session cookies directly)

## Quick Start
```bash
# From album file
python tools/download_engagement_reel.py albums/sample_album.md

# Direct artist name
python tools/download_engagement_reel.py --artist "The Weeknd"

# Download 5 reels instead of default 3
python tools/download_engagement_reel.py --artist "Kanye West" --count 5

# Custom output location
python tools/download_engagement_reel.py --artist "Drake" --output-dir .tmp/engagement/drake
```

## How It Works

### Strategy 1: Instagram Web API (Primary)
1. Reads Instagram session cookies from Firefox's cookie database
2. Hits Instagram's internal `tags/web_info` API for hashtags related to the artist:
   - `#{artist}` — general artist tag
   - `#{artist}meme` — meme content
   - `#{artist}memes` — plural variant
   - `#{artist}edit` — fan edits
3. Extracts all video/reel posts from the "top" section of each hashtag
4. Sorts by engagement (likes + comments), deduplicates
5. Downloads the top N reels via yt-dlp using the Instagram permalink

### Strategy 2: YouTube Fallback
If Instagram search fails or returns too few results:
1. Searches YouTube for: `"{artist} meme compilation"`, `"{artist} funny moments"`, etc.
2. Downloads a 30-second clip from the first match
3. Fills remaining slots until the requested count is met

## Pipeline Steps

### Step 1: Run the Tool
```bash
python tools/download_engagement_reel.py --artist "The Weeknd"
```

### Step 2: Verify Output
- Check files in `.tmp/engagement/`
- Verify file sizes are reasonable (>100KB, <100MB)
- Preview videos to confirm they're relevant and engaging

### Step 3: Re-encode for Reel Format
Downloaded engagement reels are often VP9 codec and/or square (1:1) aspect ratio. Instagram requires **H.264** video at **9:16 (1080x1920)** to post as a reel — otherwise it silently posts as a regular video, which hurts algorithmic reach.

**Always re-encode before posting:**
```bash
ffmpeg -y -i .tmp/engagement/engagement_reel_N.mp4 \
  -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" \
  -c:v libx264 -profile:v high -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 128k -movflags +faststart \
  .tmp/engagement/engagement_reel_N_reel.mp4
```
This scales the video to fit within 1080x1920 and adds black padding bars as needed.

### Step 4: Post as Reel
- **Repost** with credit: `python tools/post_to_instagram.py reel .tmp/engagement/engagement_reel_N_reel.mp4 --caption "Caption here"`
- **Use as B-Roll** in your own content
- **Share to story** with commentary

## Flags
| Flag | Description |
|------|-------------|
| `--artist "Name"` | Specify artist directly (skips markdown parsing) |
| `--output-dir path` | Output directory (default: `.tmp/engagement`) |
| `--count N` | Number of reels to download (default: 3) |
| `--skip-instagram` | Skip Instagram, go straight to YouTube fallback |

## Troubleshooting

### "No Instagram session found in Firefox"
- Open Firefox and log into Instagram
- The tool reads cookies directly from Firefox's database — no extra setup needed
- Make sure you're logged in (not just visiting the login page)

### yt-dlp format errors on some reels
- Instagram serves DASH streams (separate video + audio)
- The tool uses `bestvideo+bestaudio/best` which handles this
- If issues persist, update yt-dlp: `pip install -U yt-dlp`

### Hashtag returns few/no reels
- Some niche artists may not have dedicated meme hashtags
- The tool automatically checks 4 hashtag variants
- YouTube fallback fills remaining slots

### "Output already exists"
- The tool won't overwrite existing files. Clear the output directory:
  ```bash
  rm -f .tmp/engagement/*.mp4
  ```

## Known Limitations
- Requires Firefox with an active Instagram login (session cookies must be fresh)
- Instagram's `tags/web_info` endpoint returns ~5 top posts per hashtag (~20 candidates across 4 hashtags)
- Cookie-based auth may break if Instagram changes their web API (update the tool if endpoints change)
- YouTube fallback only grabs 30-second clips

## Lessons Learned
- The Instagram API with Instagram Login (`IGAA` tokens) does NOT support hashtag search or Business Discovery — those require the legacy Facebook-based Graph API
- Instaloader's login flow is broken as of 2026 — Instagram silently blocks API access even with valid credentials
- Direct cookie injection from Firefox + Instagram's internal web API is the most reliable scraping approach
- yt-dlp needs `bestvideo+bestaudio/best` format selector for Instagram (not `best[height<=1080]`) because Instagram serves DASH streams
- Downloaded reels are often VP9 codec and square (720x720). Posting these directly with `media_type: "REELS"` still results in Instagram treating them as regular video posts, not reels. **Always re-encode to H.264 1080x1920 (9:16) before posting** — see Step 3 above
