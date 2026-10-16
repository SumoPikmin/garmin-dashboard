"""One-time Garmin Connect login. Run in a terminal:

    .venv\\Scripts\\python login.py            web login routes only (default, avoids the rate-limited mobile route)
    .venv\\Scripts\\python login.py --all      also try the mobile routes first
"""
import sys

from garmin_dash.client import LoginFailed, interactive_login
from garmin_dash.config import TOKEN_DIR

if __name__ == "__main__":
    use_all = "--all" in sys.argv
    if not use_all:
        print("Using the web login routes (they wait 10-20 s on purpose to look like a browser).")
    try:
        client = interactive_login(skip_mobile=not use_all)
    except LoginFailed as e:
        print(f"\nLOGIN FAILED: {e}")
        sys.exit(1)
    print(f"\nOK - logged in as {client.get_full_name()}. Tokens saved to {TOKEN_DIR}.")
    print("Next: .venv\\Scripts\\python sync.py   (first run pulls ~2 years, takes a few minutes)")
