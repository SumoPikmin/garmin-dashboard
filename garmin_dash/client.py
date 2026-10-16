"""Garmin Connect login. Credentials are only used once; afterwards tokens in TOKEN_DIR are reused."""
from __future__ import annotations

import contextlib
import getpass
import re
from typing import Iterator

from garminconnect import Garmin, GarminConnectTooManyRequestsError

from .config import DATA_DIR, TOKEN_DIR

# garminconnect tries 5 login routes in order. The two mobile ones are the ones Garmin
# rate-limits hardest (HTTP 429), and every retry against them extends the block.
MOBILE_STRATEGIES = {"mobile+cffi", "mobile+requests"}
LAST_PAGE = DATA_DIR / "last_login_page.html"


class NotLoggedIn(RuntimeError):
    pass


class LoginFailed(RuntimeError):
    pass


def get_client() -> Garmin:
    """Return a logged-in client from saved tokens (never prompts)."""
    if not TOKEN_DIR.exists():
        raise NotLoggedIn(
            f"No Garmin tokens found in {TOKEN_DIR}. Run `python login.py` once in a terminal."
        )
    client = Garmin()
    try:
        client.login(str(TOKEN_DIR))
    except Exception as e:  # tokens expired or revoked
        raise NotLoggedIn(f"Saved Garmin tokens did not work ({e}). Run `python login.py` again.") from e
    return client


@contextlib.contextmanager
def _capture_sso_pages() -> Iterator[list[tuple[str, int, str]]]:
    """Record Garmin SSO responses during login so a failure can say *why*.

    The widget route answers a rejected login with the sign-in form again
    (title 'GARMIN Authentication Application'); the real reason is only in the page body.
    """
    pages: list[tuple[str, int, str]] = []
    patched = []
    sessions = []
    with contextlib.suppress(ImportError):
        from curl_cffi import requests as cffi_requests
        sessions.append(cffi_requests.Session)
    import requests
    sessions.append(requests.Session)
    for cls in sessions:
        original = cls.post

        def post(self, url, *args, _original=original, **kwargs):
            r = _original(self, url, *args, **kwargs)
            if "/sso/" in str(url):
                pages.append((str(url), getattr(r, "status_code", 0), getattr(r, "text", "") or ""))
            return r
        cls.post = post
        patched.append((cls, original))
    try:
        yield pages
    finally:
        for cls, original in patched:
            cls.post = original


def _diagnose(err: Exception, pages: list[tuple[str, int, str]]) -> str:
    text = str(err)
    html = next((body for url, _, body in reversed(pages) if "signin" in url and "<html" in body.lower()), "")
    if html:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LAST_PAGE.write_text(html, encoding="utf-8")
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    page = re.sub(r"<[^>]+>", " ", page)
    page = re.sub(r"\s+", " ", page).lower()

    if "captcha" in html.lower():
        return ("Garmin wants a CAPTCHA (bot check), usually triggered by repeated or blocked login attempts.\n"
                "  -> Sign in once at https://connect.garmin.com in a normal browser, wait about 1 hour, then run login.py again.")
    if re.search(r"(invalid|incorrect).{0,40}(sign.?in|username|password|email|credentials)", page):
        return ("Garmin rejected the email/password combination.\n"
                "  -> Check them by signing in at https://connect.garmin.com, then run login.py again.")
    if "locked" in page:
        return "Garmin says the account is locked. Unlock it via https://connect.garmin.com (password reset), then retry."
    if isinstance(err, GarminConnectTooManyRequestsError) or "429" in text:
        return ("Every login route was rate limited (HTTP 429). This is a temporary block by Garmin on this "
                "network/client, made longer by each retry.\n"
                "  -> Wait 1-2 hours without retrying, then run login.py again (or try once from another network, e.g. phone hotspot).")
    detail = f"\n  Garmin's sign-in page was saved to {LAST_PAGE} for inspection." if html else ""
    return f"Login failed: {text}{detail}"


def interactive_login(skip_mobile: bool = True, token_dir=None) -> Garmin:
    """Prompt for email/password (+ MFA code if enabled) and save tokens (to token_dir or TOKEN_DIR)."""
    token_dir = token_dir or TOKEN_DIR
    email = input("Garmin Connect email: ").strip()
    password = getpass.getpass("Garmin Connect password (hidden): ")
    client = Garmin(email, password, prompt_mfa=lambda: input("MFA code from email/app: ").strip())
    if skip_mobile:
        client.client.skip_strategies = set(MOBILE_STRATEGIES)
    token_dir.parent.mkdir(parents=True, exist_ok=True)
    with _capture_sso_pages() as pages:
        try:
            client.login(str(token_dir))
        except Exception as e:
            raise LoginFailed(_diagnose(e, pages)) from e
    return client
