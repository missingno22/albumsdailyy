# Create and Post Reel to Instagram

## Objective
Generate a reel from album rankings and automatically post it to Instagram with a custom caption — all in one command.

## Prerequisites
- All reel generation dependencies installed (`yt-dlp`, `ffmpeg`, `moviepy`, etc.)
- `.env` configured with:
  - `INSTAGRAM_USER_ID` — your Instagram user ID
  - `INSTAGRAM_ACCESS_TOKEN` — a valid long-lived token (60-day expiry)
  - `GOOGLE_DRIVE_QUEUE_FOLDER_ID` — Drive folder for temporary video hosting
- `credentials.json` in project root (Google OAuth)
- Instagram account is Business or Creator type

## Quick Start
```bash
python tools/create_and_post_reel.py albums/my_ranking.md --caption "Every song on Album by Artist, rated 🎶"
```

## Steps

### 1. Prepare your rankings file
Create a markdown file in `albums/` with the standard format:
```markdown
# Album Title
## Artist Name
1) Song Name - 8/10
2) Another Song - 7/10
```

### 2. Run the pipeline
```bash
# Full reel (all songs, ~60-90s)
python tools/create_and_post_reel.py albums/my_ranking.md --caption "Your caption here"

# Short reel (top 5 only)
python tools/create_and_post_reel.py albums/my_ranking.md --caption "Your caption" --reel-type short

# Draft mode (fast render for testing — does NOT post)
python tools/create_and_post_reel.py albums/my_ranking.md --caption "Test" --draft
```

### 3. What happens
1. **Parse** — extracts songs, ratings, album info from markdown
2. **Download audio** — finds and downloads clips from YouTube
3. **Download B-Roll** — grabs video clips for backgrounds
4. **Calculate timing** — determines duration per song segment
5. **Compose reel** — renders the final video
6. **Upload to Drive** — temporarily hosts video at a public URL
7. **Post to Instagram** — creates container → waits for processing → publishes
8. **Cleanup** — deletes temporary Drive file (unless `--keep-drive-file`)

### 4. Post-only mode
If you already generated a reel and just want to post it:
```bash
python tools/post_to_instagram.py .tmp/output/reel_final.mp4 --caption "Your caption"
```

Or use the combined script with `--skip-generate`:
```bash
python tools/create_and_post_reel.py albums/my_ranking.md --skip-generate --caption "Your caption"
```

## Getting Your Instagram User ID
If `INSTAGRAM_USER_ID` is not set, run:
```
GET https://graph.instagram.com/v25.0/me?access_token={YOUR_TOKEN}
```
The `id` field in the response is your user ID.

## Troubleshooting

### "Invalid video URL"
- Google Drive file must be publicly shared (the script handles this automatically)
- If Instagram rejects the URL, try `--keep-drive-file` and verify the link works in a browser

### "Media creation timeout"
- Instagram can take 1-5 minutes for longer videos
- The script polls for up to 5 minutes. If it times out, the container may still be processing
- You can check manually: `GET https://graph.instagram.com/v25.0/{container_id}?fields=status_code&access_token={TOKEN}`

### "Permission denied" / token errors
- Token expires every 60 days — refresh it per `workflows/n8n_setup.md` Token Refresh section
- Verify account is still Business/Creator in Instagram settings

### Reel generates but posting fails
- The reel is preserved at `.tmp/output/reel_final.mp4`
- Retry with: `python tools/post_to_instagram.py .tmp/output/reel_final.mp4 --caption "..."`
