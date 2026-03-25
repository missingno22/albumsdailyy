"""
Calculate timing for the short reel format (top 5 songs).

Usage: python tools/short_reel/calculate_short_timing.py [album_data_json] [output_json]

Selects the 5 highest-rated songs from the album,
then assigns escalating durations to build anticipation.
Output: .tmp/short_timing.json
"""

import json
import os
import sys


def calculate_short_timing(data):
    songs = data["songs"]  # already sorted worst-to-best
    total_songs = len(songs)

    # Select top 5 songs (or all if fewer than 5)
    num_top = min(5, total_songs)
    if total_songs <= 5:
        print(f"Warning: Album only has {total_songs} songs. Using all songs.")
        selected = list(enumerate(songs))
    else:
        selected = [(i, songs[i]) for i in range(total_songs - num_top, total_songs)]

    crossfade = 0.3

    # Timing: escalating durations — #5 gets least, #1 gets most
    # Durations: 2.5s, 3.0s, 3.5s, 4.0s, 5.0s
    top_durations = [2.5, 3.0, 3.5, 4.0, 5.0]

    segments = []
    current_time = 0.0

    for seg_idx, (song_index, song) in enumerate(selected):
        # Calculate countdown number from original album position
        # Songs are sorted worst-to-best, so song at index 0 is the worst (highest countdown)
        countdown = total_songs - song_index

        duration = top_durations[seg_idx] if seg_idx < len(top_durations) else 3.5

        segments.append({
            "song_index": song_index,
            "name": song["name"],
            "rating": song["rating"],
            "duration": round(duration, 2),
            "start_time": round(current_time, 2),
            "countdown_number": countdown,
        })

        current_time += duration

    body_duration = round(current_time, 2)

    return {
        "total_duration": body_duration,
        "crossfade": crossfade,
        "total_songs": total_songs,
        "selected_count": len(selected),
        "segments": segments,
    }


def main():
    album_path = sys.argv[1] if len(sys.argv) > 1 else ".tmp/album_data.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else ".tmp/short_timing.json"

    if not os.path.exists(album_path):
        print(f"Error: Album data not found: {album_path}")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(album_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    timing = calculate_short_timing(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(timing, f, indent=2, ensure_ascii=False)

    print(f"Short reel timing calculated: {timing['total_duration']}s body")
    print(f"  {timing['selected_count']} of {timing['total_songs']} songs selected (top 5)")
    print(f"  + title card (~3s) + end card (~6s) - crossfades = ~25-30s total")
    print()
    for seg in timing["segments"]:
        print(f"  #{seg['countdown_number']:2d} | {seg['duration']:5.2f}s | {seg['rating']}/10 | {seg['name']}")
    print(f"\nOutput: {output_path}")


if __name__ == "__main__":
    main()
