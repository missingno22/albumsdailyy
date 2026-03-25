"""
Backward-compatible wrapper for test_endcard.

The actual implementation has moved to tools/full_reel/test_endcard.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from full_reel.test_endcard import main

if __name__ == "__main__":
    main()
