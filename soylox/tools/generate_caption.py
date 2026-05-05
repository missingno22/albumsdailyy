"""
Generate an IG caption for a trend-repost reel via OpenAI GPT-4o-mini.

Input: trend dict + original video metadata (uploader, title, description).
Output: {"caption": "...", "hashtags": ["#..."]}

Usage:
    python soylox/tools/generate_caption.py --trend .tmp/trends_*.json --index 0 \
        --meta '{"uploader":"someone","title":"some tiktok"}'
"""

import argparse
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SOYLOX_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    env_path = os.path.abspath(os.path.join(SOYLOX_ROOT, "..", ".env"))
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and v.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()


SYSTEM_PROMPT = """You write Instagram Reels content for a Gen Z / Gen Alpha brainrot meme account. Your job: maximize watch-time + shares by giving each reel (1) a punchy burned-in HOOK that stops the scroll in the first second, and (2) a short caption that adds flavor without being a wall of text.

Output STRICT JSON only:
{
  "hook": string (2-6 WORDS, ALL CAPS, meme-y / unhinged / curiosity gap — this gets BURNED ON-SCREEN big for first 2.5s, so it MUST hit instantly; no emoji, no hashtags, no punctuation except ? or !),
  "caption": string (1-2 sentences, 80-140 chars max, brainrot voice with informational flavor; can include 1-2 emoji; assume the viewer saw the clip),
  "hashtags": array of 8-12 strings (each starts with #; see hashtag rules below)
}

HOOK examples (what kind of energy we want — DON'T copy literally):
- "POV: YOU'RE IN OHIO"
- "SKIBIDI LORE UPDATE"
- "WHY IS BRO LIKE THIS"
- "HE COOKED FR"
- "AURA: -9999"
- "THIS IS NPC BEHAVIOR"
- "GEN ALPHA SEND HELP"
- "CERTIFIED GYATT MOMENT"
- "MOGGING 101"
- "CAUGHT IN 4K"

Hook rules:
- 2-6 WORDS ONLY. Hard cap at 24 CHARACTERS total (including spaces). Shorter hits harder and fits on screen.
- ALL CAPS. No periods. A ? or ! is fine.
- NEVER space out letters for stylistic effect (no "D A G E S T A N", no "B R U H"). Words stay as normal words — spacing breaks the on-screen layout.
- Create curiosity, tension, or absurdity in the first second. The viewer should think "wait what" and stay.
- Brainrot vocab encouraged: rizz, gyatt, skibidi, ohio, aura, mewing, fanum tax, delulu, sigma, cooked, npc behavior, mogging, unc, crashout, tweaking, 6'7, 67, pookie, goat.
- NEVER use boomer phrases: "you won't believe", "watch till the end", "wait for it", "check this out", "amazing", "epic", "insane" — BANNED.

Caption rules:
- 80-140 chars. No paragraph walls. Lowercase preferred.
- Can drop a fun factoid or just commentary, brainrot voice.
- 1-2 emoji MAX. No hashtags inside the caption body.
- Never clickbait. Never describe what's on screen — ASSUME they saw it.

Hashtag rules (CRITICAL for reach — these are IG-native, NOT tiktok tags):
- MUST include: #memepage, #brainrot, #memes
- Add 5-8 more from: #genz, #genalpha, #meme, #funny, #shitpost, #dankmemes, #viralreels, #explore, #explorepage, #instareels, plus 1-2 trend-specific tags
- DO NOT include #fyp, #viral, #reels — these are TikTok-native and hurt IG reach on new accounts. BANNED.

Example output:
{
  "hook": "HE COOKED THE ENTIRE LOBBY",
  "caption": "generational talent behavior from a 13 year old with a headset fr. the aura emanating from this clip is measurable in joules",
  "hashtags": ["#memepage", "#brainrot", "#memes", "#genz", "#genalpha", "#gaming", "#dankmemes", "#shitpost", "#funny"]
}"""


def _build_user_prompt(trend, meta):
    parts = [
        f"Trend: {trend.get('label', '')}",
        f"Source: {trend.get('source', 'unknown')}",
        f"Velocity score: {trend.get('velocity', 0):.0f}",
    ]
    if meta:
        if meta.get("uploader"):
            parts.append(f"Original creator: {meta['uploader']}")
        if meta.get("title"):
            parts.append(f"Original title: {meta['title'][:120]}")
        if meta.get("description") and meta.get("description") != meta.get("title"):
            parts.append(f"Original description: {meta['description'][:200]}")
    parts.append("\nWrite the JSON.")
    return "\n".join(parts)


def generate_caption(trend, meta=None, model="gpt-4o-mini"):
    _load_env()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing in .env")

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed")

    client = OpenAI(api_key=api_key)
    print(f"[caption] Generating for '{trend.get('label')}' via {model}", flush=True)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(trend, meta)},
        ],
        response_format={"type": "json_object"},
        temperature=1.0,   # higher = more unhinged, which is the point
        max_tokens=500,
    )
    data = json.loads(resp.choices[0].message.content)

    # Hook — burn-in on-screen text, short + punchy. Hard cap at 40 chars to
    # guarantee it fits one line of large drawtext. Strip trailing punctuation
    # except ? / !.
    hook = str(data.get("hook", "")).strip().upper()
    # Collapse "D A G E S T A N"-style spaced letters back into a word — the
    # model sometimes sneaks this in despite the prompt and it breaks layout.
    hook = re.sub(r"\b(?:[A-Z0-9] ){2,}[A-Z0-9]\b",
                  lambda m: m.group(0).replace(" ", ""), hook)
    hook = re.sub(r"\s+", " ", hook).strip()
    hook = re.sub(r"[\.,;:]+$", "", hook).strip()
    # Hard width cap: 24 chars max. If we'd lose meaning, truncate at last space.
    if len(hook) > 24:
        cut = hook[:24]
        last_space = cut.rfind(" ")
        hook = cut[:last_space] if last_space > 8 else cut

    # Caption — shorter now (140 char soft cap, 180 hard cap) so IG users
    # don't skip past the tap-to-expand fold.
    caption = str(data.get("caption", trend.get("label", ""))).strip()[:180]

    tags = data.get("hashtags", []) or []
    hashtags = [t if t.startswith("#") else f"#{t}" for t in tags][:14]

    # IG-native required tags (replace the old TikTok-native set).
    for required in ("#memepage", "#brainrot", "#memes"):
        if required not in hashtags:
            hashtags.append(required)
    # Strip any TikTok tags that slipped through — they throttle IG reach.
    banned = {"#fyp", "#viral", "#reels", "#foryou", "#foryoupage"}
    hashtags = [h for h in hashtags if h.lower() not in banned]

    result = {"hook": hook, "caption": caption, "hashtags": hashtags}
    print(f"[caption] hook: {hook}", flush=True)
    print(f"[caption] {caption}", flush=True)
    print(f"[caption] tags: {' '.join(hashtags)}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trend")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--label")
    parser.add_argument("--meta", help="JSON string of video metadata")
    args = parser.parse_args()

    if args.trend:
        with open(args.trend, "r", encoding="utf-8") as f:
            trends = json.load(f)
        trend = trends[args.index]
    elif args.label:
        trend = {"label": args.label, "slug": re.sub(r"[^a-z0-9]+", "-", args.label.lower()).strip("-")}
    else:
        parser.error("Provide --trend+--index or --label")

    meta = json.loads(args.meta) if args.meta else None
    data = generate_caption(trend, meta=meta)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
