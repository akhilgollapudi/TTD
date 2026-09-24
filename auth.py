import re

if __package__:
    from .config import *
    from .utils import norm, matches, body_text, snapshot
else:
    from config import *
    from utils import norm, matches, body_text, snapshot

"""Persistent browser authentication helpers."""

def save_auth_state(context):
    """Save a diagnostic/auth backup without using it to overwrite the live profile.

    The primary session store is the persistent Chromium profile itself.
    TTD may keep authentication in cookies/localStorage and can also use
    browser/session state that should not be reconstructed by manually adding
    cookies from an older snapshot.
    """
    try:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(TTD_AUTH_STATE_FILE))
    except Exception as exc:
        print(f"[WARN] Auth-state backup failed: {exc}")

def restore_auth_cookies(context):
    """Deprecated: do not inject stale cookies into a persistent TTD profile.

    launch_persistent_context() already reuses the profile directory, including
    the site's normal browser storage. Injecting cookies from a previous
    snapshot can create a mixed/stale authentication state and trigger another
    OTP. This function is retained only for compatibility with older code.
    """

def ttd_login_screen(page):
    """Return True only when the page looks like an actual TTD login/OTP screen."""
    try:
        url = page.url.lower()
        body = norm(page.locator("body").inner_text(timeout=3000))
        return (
            "/login" in url or
            "/signin" in url or
            bool(re.search(
                r"enter\s*otp|send\s*otp|verify.*mobile|mobile.*number",
                body,
                re.I
            ))
        )
    except Exception:
        return False

def save_auth_if_authenticated(context, page):
    """Persist the current browser profile/backup only when not on OTP/login."""
    try:
        if not ttd_login_screen(page):
            save_auth_state(context)
    except Exception:
        pass

def wait_for_auth_completion(page, context):
    """Wait for the user to complete OTP/CAPTCHA once, then persist the session.

    This does not attempt to solve or bypass OTP/CAPTCHA. It simply keeps the
    same persistent browser profile alive and saves the authenticated state
    after the user completes the normal TTD login flow.
    """
    if not ttd_login_screen(page):
        return True

    print("[AUTH] TTD login/OTP is currently required.")
    print("[AUTH] Complete OTP/CAPTCHA manually in the SAME browser window.")
    print("[AUTH] After the TTD dashboard/booking page appears, press ENTER here.")
    input("[AUTH] Press ENTER after authentication is complete: ")

    try:
        if ttd_login_screen(page):
            print("[AUTH] Login/OTP screen is still visible.")
            print("[AUTH] Session was not marked authenticated; leaving browser open.")
            return False
    except Exception:
        return False

    save_auth_state(context)
    print("[AUTH] Authentication completed; session saved.")
    return True

