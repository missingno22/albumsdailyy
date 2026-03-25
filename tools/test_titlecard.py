"""
Backward-compatible wrapper for test_titlecard.

The actual implementation has moved to tools/full_reel/test_titlecard.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from full_reel.test_titlecard import main

if __name__ == "__main__":
    main()
