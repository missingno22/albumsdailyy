"""
Backward-compatible wrapper for calculate_timing.

The actual implementation has moved to tools/full_reel/calculate_full_timing.py.
This wrapper ensures `python tools/calculate_timing.py` still works.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from full_reel.calculate_full_timing import calculate_timing, main

if __name__ == "__main__":
    main()
