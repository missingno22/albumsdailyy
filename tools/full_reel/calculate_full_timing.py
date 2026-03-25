"""
Calculate timing/duration for each song segment in the full reel.

Usage: python tools/full_reel/calculate_full_timing.py [album_data_json] [output_json]

Scales total reel length based on number of songs.
Bottom songs get ~5s, top 5 get weighted longer segments.
Output: .tmp/timing.json
"""

import json
import os
import sys


def calculate_timing(data):
    songs = data["songs"]  # already sorted worst-to-best
    total_songs = len(songs)

    crossfade = 0.3
    top_n = min(5, total_songs)
    bottom_n = total_songs - top_n

    short_duration = 5.0  # seconds per bottom-ranked song

    # Time used by bottom songs
    bottom_time = bottom_n * short_duration

    # Top songs get weighted time, clamped so each gets roughly 5-10s
    min_top = top_n * 5.0
    max_top = top_n * 8.0
    top_time = max(min_top, min(max_top, top_n * 7.0))
    total_duration = bottom_time + top_time

    # Distribute top time with increasing weight (best song gets most)
    weights = list(range(3, 3 + top_n))
    weight_sum = sum(weights)
    top_durations = [(w / weight_sum) * top_time for w in weights]

    # Build segments
    segments = []
    current_time = 0.0
    countdown = total_songs

    for i, song in enumerate(songs):
        if i < bottom_n:
            duration = short_duration
        else:
            duration = top_durations[i - bottom_n]

        segments.append({
            "song_index": i,
            "name": song["name"],
            "rating": song["rating"],
            "duration": round(duration, 2),
            "start_time": round(current_time, 2),
            "countdown_number": countdown,
        })

        current_time += duration
        countdown -= 1

    return {
        "total_duration": round(current_time, 2),
        "crossfade": crossfade,
        "total_songs": total_songs,
        "segments": segments,
    }


def main():
    album_path = sys.argv[1] if len(sys.argv) > 1 else ".tmp/album_data.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else ".tmp/timing.json"

    if not os.path.exists(album_path):
        print(f"Error: Album data not found: {album_path}")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(album_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    timing = calculate_timing(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(timing, f, indent=2, ensure_ascii=False)

    print(f"Timing calculated: {timing['total_duration']}s total")
    print(f"  {timing['total_songs']} segments")
    for seg in timing["segments"]:
        print(f"  #{seg['countdown_number']:2d} | {seg['duration']:5.2f}s | {seg['rating']}/10 | {seg['name']}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
