"""
Backward-compatible wrapper for compose_reel.

The actual implementation has moved to tools/full_reel/compose_full_reel.py.
This wrapper ensures `python tools/compose_reel.py` still works.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from full_reel.compose_full_reel import build_title_card, main
from shared import (
    rating_color, RATING_LEGEND, find_peak_segment, crop_to_vertical,
    wrap_text, make_text_clip, plan_broll_assignments,
    build_end_card, build_segment,
    WIDTH, HEIGHT, FPS, FONT_BOLD, FONT_REGULAR, FONT_IMPACT, FONT_DISPLAY,
)

if __name__ == "__main__":
    main()
