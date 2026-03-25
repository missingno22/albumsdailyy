"""
Parse an album ranking markdown file into structured JSON.

Usage: python tools/parse_markdown.py <input_markdown> [output_json]

Input format:
    # Album Title
    ## Artist Name
    1) Song Name - 8/10
    2) Another Song - 7/10
    ...

Output: .tmp/album_data.json (or custom path)
"""

import json
import re
import sys
import os


def parse_album_markdown(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    album = None
    artist = None
    album_number = None
    songs = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        # H1: Album title
        if line.startswith("# ") and not line.startswith("## "):
            album = line[2:].strip()
            continue

        # H2: Artist name or album number
        if line.startswith("## "):
            value = line[3:].strip()
            # If it's just a number, it's the album number
            if re.match(r"^\d+$", value):
                album_number = int(value)
            else:
                artist = value
            continue

        # Song line: N) Song Name - R/10
        match = re.match(r"(\d+)\)\s+(.+?)\s*-\s*(\d+(?:\.\d+)?)/10", line)
        if match:
            rank = int(match.group(1))
            name = match.group(2).strip()
            rating = float(match.group(3))
            songs.append({"rank": rank, "name": name, "rating": rating})
            continue

    # Validation
    if not album:
        print("Error: No album title found (expected '# Album Title')")
        sys.exit(1)
    if not artist:
        print("Error: No artist name found (expected '## Artist Name')")
        sys.exit(1)
    if not songs:
        print("Error: No songs found (expected 'N) Song Name - R/10')")
        sys.exit(1)

    # Sort worst to best (ascending by rating, ties broken by higher rank number first)
    songs.sort(key=lambda s: (s["rating"], -s["rank"]))

    result = {
        "album": album,
        "artist": artist,
        "songs": songs,
        "total_songs": len(songs),
    }
    if album_number is not None:
        result["album_number"] = album_number
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/parse_markdown.py <input_markdown> [output_json]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else ".tmp/album_data.json"

    if not os.path.exists(input_path):
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    data = parse_album_markdown(input_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Parsed '{data['album']}' by {data['artist']}")
    print(f"  {data['total_songs']} songs, sorted worst-to-best")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
