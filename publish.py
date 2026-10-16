"""Hosted (GitHub Pages) version of the dashboard, password protected by encryption.

Local, once:
    .venv\\Scripts\\python publish.py login-cloud     separate Garmin login for GitHub (own token, so
                                                     your PC and GitHub never invalidate each other)
    .venv\\Scripts\\python publish.py bundle          -> state.enc (DB + cloud token + settings, encrypted)

GitHub Actions (see .github/workflows/sync.yml):
    publish.py restore state.enc   ->  sync.py  ->  publish.py build site  ->  publish.py save state.enc

Local preview of the built site:
    .venv\\Scripts\\python publish.py build site      (asks for the password if DASHBOARD_PASSWORD is unset)
"""
from __future__ import annotations

import getpass
import io
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

from garmin_dash import analytics, crypto, db
from garmin_dash.config import DATA_DIR, ROOT, SETTINGS_PATH, TOKEN_DIR

CLOUD_TOKENS = DATA_DIR / "cloud_tokens"
STATE_KEY_FILE = DATA_DIR / "state_key.txt"
STATIC = ROOT / "static"


def state_key() -> str:
    key = os.getenv("STATE_KEY")
    if key:
        return key.strip()
    if not STATE_KEY_FILE.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_KEY_FILE.write_text(crypto.new_state_key(), encoding="utf-8")
        print(f"Created a new state key in {STATE_KEY_FILE} (this becomes the STATE_KEY GitHub secret).")
    return STATE_KEY_FILE.read_text(encoding="utf-8").strip()


def bundle(out: Path, tokens_dir: Path) -> None:
    db_path = DATA_DIR / "garmin.sqlite"
    token_files = sorted(p for p in tokens_dir.glob("*") if p.is_file()) if tokens_dir.exists() else []
    if not db_path.exists() or not token_files:
        sys.exit(f"Need {db_path} and Garmin tokens in {tokens_dir} (run login-cloud first).")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(db_path, arcname="garmin.sqlite")
        for p in token_files:
            tar.add(p, arcname=f"tokens/{p.name}")
        if SETTINGS_PATH.exists():
            tar.add(SETTINGS_PATH, arcname="settings.json")
    out.write_bytes(crypto.encrypt_state(buf.getvalue(), state_key()))
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB, encrypted).")


def restore(src: Path) -> None:
    raw = crypto.decrypt_state(src.read_bytes(), state_key())
    allowed = {"garmin.sqlite": DATA_DIR / "garmin.sqlite", "settings.json": SETTINGS_PATH}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            if m.name.startswith("tokens/") and "/" not in m.name[7:] and ".." not in m.name:
                dest = TOKEN_DIR / m.name[7:]
            elif m.name in allowed:
                dest = allowed[m.name]
            else:
                continue  # ignore anything unexpected
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(m).read())
    print(f"Restored database, settings and Garmin token to {DATA_DIR} / {TOKEN_DIR}.")


def build(out: Path) -> None:
    password = os.getenv("DASHBOARD_PASSWORD") or getpass.getpass("Dashboard password (hidden): ")
    if len(password) < 12:
        sys.exit("Use a dashboard password of at least 12 characters - the encrypted file is public.")
    payload = analytics.build_dashboard(db.connect(readonly=True))
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out / "static")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", '<head>\n  <meta name="robots" content="noindex, nofollow">\n'
                                  '  <script>window.GARMIN_STATIC = true;</script>', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "static" / "index.html").unlink()
    (out / "data.enc").write_text(json.dumps(crypto.encrypt_for_browser(payload, password, crypto.deployment_salt(state_key()))), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built {out} (encrypted dashboard data: {(out / 'data.enc').stat().st_size // 1024} KB).")


def login_cloud() -> None:
    from garmin_dash.client import LoginFailed, interactive_login
    print("This creates a second Garmin login just for GitHub, so it never logs your PC out.")
    try:
        interactive_login(token_dir=CLOUD_TOKENS)
    except LoginFailed as e:
        sys.exit(f"\nLOGIN FAILED: {e}")
    print(f"\nOK - cloud token saved to {CLOUD_TOKENS}. Next: .venv\\Scripts\\python publish.py bundle")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["help"]
    if cmd == "login-cloud":
        login_cloud()
    elif cmd == "bundle":
        bundle(Path(rest[0] if rest else "state.enc"), CLOUD_TOKENS)
    elif cmd == "save":
        bundle(Path(rest[0] if rest else "state.enc"), TOKEN_DIR)
    elif cmd == "restore":
        restore(Path(rest[0] if rest else "state.enc"))
    elif cmd == "build":
        build(Path(rest[0] if rest else "site"))
    else:
        print(__doc__)
