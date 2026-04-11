"""
Start the Reel Scheduler web server.

Usage:
    python run_flask.py
    python run_flask.py --port 5052

Then visit http://localhost:5051 in your browser.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

from flask_app.app import create_app


def main():
    parser = argparse.ArgumentParser(description="Reel Scheduler Server")
    parser.add_argument("--port", type=int, default=5051, help="Port (default: 5051)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    app = create_app()
    print(f"\nReel Scheduler running at http://localhost:{args.port}")
    print(f"Press Ctrl+C to stop.\n")
    app.run(host=args.host, port=args.port, debug=True)


if __name__ == "__main__":
    main()
