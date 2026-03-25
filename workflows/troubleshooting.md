# Troubleshooting Guide

## yt-dlp Issues

### Song not found on YouTube
- Try alternate queries: add "official audio", "lyrics", or "topic" to search
- Ask user for a direct YouTube URL as fallback
- Check if the song name has special characters that need escaping

### Rate limiting
- Add `--sleep-interval 2` to yt-dlp calls
- Wait 30-60 seconds between batch downloads
- If persistent, try with `--cookies-from-browser chrome`

### Download timeout
- Increase `--socket-timeout` value
- Check internet connection
- Try a different video quality (`-f worst` for testing)

## moviepy / ffmpeg Issues

### Rendering crashes
- Verify ffmpeg path: `ffmpeg -version`
- Check available disk space (need ~500MB free for rendering)
- Try lower resolution first: change WIDTH/HEIGHT to 720x1280 in compose_reel.py
- Check if B-Roll clips are corrupted: `ffprobe <clip.mp4>`

### Audio/video sync issues
- Ensure all clips use same fps (30)
- Check audio sample rates are consistent (44100 Hz)
- Try rendering without crossfade first (set crossfade to 0)

### Text rendering fails
- Check font availability: `fc-list | grep Arial`
- Fallback fonts: "DejaVu-Sans-Bold", "Helvetica-Bold", "Liberation-Sans-Bold"
- On Windows, fonts should be available at `C:/Windows/Fonts/`

## Output Issues

### File too large for Instagram (>100MB)
- Reduce bitrate: change `bitrate="5000k"` to `"3000k"` in compose_reel.py
- Reduce resolution to 720x1280
- Shorten total duration by adjusting calculate_timing.py

### Video quality too low
- Increase B-Roll download quality: change `720` to `1080` in download_broll.py
- Increase output bitrate to `"8000k"`

### Missing audio for some segments
- compose_reel.py will use silence for missing audio
- Re-run download_audio.py with manual YouTube URLs for failed songs
