"""Sync Garmin Connect -> data/garmin.sqlite.

    .venv\\Scripts\\python sync.py           incremental (fast)
    .venv\\Scripts\\python sync.py --full    re-download the whole history window
"""
import sys

from garmin_dash.sync import sync

if __name__ == "__main__":
    result = sync(full="--full" in sys.argv)
    if result["errors"]:
        print(f"\nDone with {len(result['errors'])} failed step(s): {', '.join(result['errors'])}")
    else:
        print("\nDone - all steps ok.")
