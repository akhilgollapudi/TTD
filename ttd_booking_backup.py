#!/usr/bin/env python3
"""
TTD Special Entry Darshan Booking Assistant v6 - HTML DOM mapped

Targets the form structure shown in the supplied TTD screenshot.

Darshan Details:
  Darshan Date | Darshan Slot | No. of Tickets | Ticket Amount
  No. of Laddus | Hundi Offerings | Total Cost

Pilgrim Details:
  Name | Age | Gender | Photo ID Proof | Photo ID Number

General Details:
  Email | City | State | Country | Pincode

Date/slot behavior:
  - Try the requested target_date.
  - If unavailable and date_fallback == NEXT_AVAILABLE, select the earliest
    enabled date after the requested date that is exposed by the live UI.
  - On the selected date, try preferred_slot.
  - If preferred_slot is unavailable and slot_fallback == EARLIEST_AVAILABLE,
    select the earliest enabled slot.
  - Never silently select an earlier date than requested.
  - If the live UI cannot be safely interpreted, stop and ask for manual selection.

OTP/CAPTCHA, anti-bot/queue controls and payment remain manual.

READY / ENTER command:
  - Press ENTER or type READY whenever you want the automation to start/restart value entry.
  - Pressing ENTER with no text is intentionally equivalent to READY.
  - The same browser/session is reused; no new browser is created.
  - After manual corrections, press ENTER or type READY again to re-enter the configured values.
  - Type CLOSE only when you explicitly want to end the script.
"""

import json, re, time
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

TTD_URL = "https://ttdevasthanams.ap.gov.in/home/dashboard"
BROWSER_PROFILE_DIR = Path.home() / "ttd_browser_profile"
TTD_AUTH_STATE_FILE = BROWSER_PROFILE_DIR / "ttd_auth_state.json"
DATA_FILE = Path(__file__).with_name("pilgrims.json")
ARTIFACT_DIR = Path(__file__).with_name("ttd_runtime")
ARTIFACT_DIR.mkdir(exist_ok=True)

FIELD_PATTERNS = {
    # Exact DOM names from the supplied TTD HTML.
    "name": [r'^\s*name\s*\*?\s*$', r'pilgrim.*name'],
    "age": [r'^\s*age\s*\*?\s*$', r'pilgrim.*age'],
    "gender": [r'^\s*gender\s*\*?\s*$', r'^\s*sex\s*$'],
    "id_type": [r'photo\s*id\s*proof', r'id\s*proof\s*type', r'proof\s*type'],
    "id_number": [r'photo\s*id\s*number', r'id\s*proof\s*number', r'proof\s*number', r'aadhaar', r'aadhar'],
    "email": [r'^\s*email\s*(address)?\s*\*?\s*$', r'e-mail'],
    "city": [r'^\s*city\s*\*?\s*$', r'town'],
    "state": [r'^\s*state\s*\*?\s*$',],
    "country": [r'^\s*country\s*\*?\s*$',],
    "pincode": [r'pincode', r'pin\s*code', r'postal\s*code', r'zip'],
    "tickets": [r'no\.?\s*of\s*tickets', r'number\s*of\s*tickets', r'tickets'],
    "laddus": [r'no\.?\s*of\s*laddus', r'number\s*of\s*laddus', r'laddu', r'laddoo'],
    "hundi": [r'hundi\s*offerings', r'hundi', r'offering'],
}

RELEASE_TEXT = [
    r"book\s*now", r"select\s*date", r"darshan\s*date",
    r"select\s*(?:time|slot)", r"available\s*quota",
    r"pilgrim\s*details", r"devotee\s*details", r"booking\s*details"
]
CLOSED_TEXT = [r"quota\s*not\s*released", r"quota\s*will\s*be\s*released", r"not\s*released"]
PAYMENT_TEXT = [
    r"\bpayment\b", r"pay\s*now", r"proceed\s*to\s*pay",
    r"payment\s*gateway", r"\bupi\b", r"credit\s*card",
    r"debit\s*card", r"net\s*banking"
]
BOOKING_IN_PROGRESS_TEXT = [
    r"pilgrims?\s+booking\s+is\s+already\s+in\s+progress",
    r"booking\s+is\s+already\s+in\s+progress",
    r"pilgrims?\s+booking\s+already\s+in\s+progress",
]

def norm(v):
    return re.sub(r"\s+", " ", (v or "").strip().lower())

def matches(text, patterns):
    text = norm(text)
    return any(re.search(p, text, re.I) for p in patterns)

def load_data():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))

def snapshot(page, label):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = ARTIFACT_DIR / f"{stamp}_{label}.png"
    html = ARTIFACT_DIR / f"{stamp}_{label}.html"
    try: page.screenshot(path=str(png), full_page=True)
    except Exception: pass
    try: html.write_text(page.content(), encoding="utf-8")
    except Exception: pass
    print(f"[DEBUG] Saved: {png}")

def body_text(page):
    try: return page.locator("body").inner_text(timeout=3000)
    except Exception: return ""

def booking_in_progress(page):
    """Detect TTD's existing-booking lock without navigating or reloading."""
    try:
        return matches(body_text(page), BOOKING_IN_PROGRESS_TEXT)
    except Exception:
        return False


def control_signature(page):
    try:
        loc = page.locator("input:visible, textarea:visible, select:visible, button:visible, a:visible")
        out = []
        for i in range(min(loc.count(), 300)):
            c = loc.nth(i)
            try:
                out.append(" | ".join([
                    c.evaluate("(e)=>e.tagName"),
                    c.get_attribute("type") or "",
                    c.get_attribute("name") or "",
                    c.get_attribute("id") or "",
                    c.get_attribute("placeholder") or "",
                    c.get_attribute("aria-label") or "",
                    norm(c.inner_text())[:100],
                ]))
            except Exception: pass
        return "\n".join(out)
    except Exception: return ""

def page_state(page):
    body = body_text(page)
    sig = control_signature(page)
    try: inputs = page.locator("input:visible, textarea:visible, select:visible").count()
    except Exception: inputs = 0
    return {
        "url": page.url,
        "body": body,
        "signature": sig,
        "closed": matches(body, CLOSED_TEXT),
        "release_text": matches(body, RELEASE_TEXT),
        "form_signal": inputs >= 2 and (
            matches(body, [r"pilgrim", r"devotee", r"darshan"]) or
            matches(sig, [r"name", r"age", r"gender", r"photo.*id", r"aadhaar"])
        )
    }

def release_detected(prev, cur):
    return (
        prev and prev["closed"] and not cur["closed"] and cur["release_text"]
    ) or (
        cur["form_signal"] and (cur["release_text"] or matches(cur["body"], [r"pilgrim", r"booking"]))
    ) or (
        prev and prev["url"] != cur["url"] and cur["form_signal"]
    )

def wait_until_start(data):
    tz = ZoneInfo(data["booking"].get("timezone", "Asia/Kolkata"))
    hh, mm = map(int, data["booking"].get("start_time", "09:20").split(":"))
    now = datetime.now(tz)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now: return
    print(f"[INFO] Waiting until {target.strftime('%H:%M')} IST...")
    while True:
        remaining = (target - datetime.now(tz)).total_seconds()
        if remaining <= 0: return
        time.sleep(min(5, remaining))

def watch_quota(page, minutes):
    """
    Watch the already-open quota page without reloading or navigating it.

    Important: TTD can redirect/rebuild the page during reloads. Reloading here
    can lose the authenticated booking page and send the browser to /temples.
    We therefore keep the exact page/session the user prepared manually.
    """
    previous = page_state(page)
    deadline = time.time() + minutes * 60
    print("[INFO] Multi-signal watcher active (no page reload/navigation).")

    while time.time() < deadline:
        time.sleep(1.0)

        # If the site itself navigates away, do not automatically recover by
        # opening/reloading another page. Preserve the user's session state.
        try:
            current_url = page.url
            if "/temples" in current_url:
                print(f"[WARN] TTD navigated to {current_url}")
                print("[ACTION] Staying on the current browser page; no automatic reload.")
                return False
        except Exception:
            pass

        current = page_state(page)

        if release_detected(previous, current):
            print("[OK] Booking/form availability detected.")
            snapshot(page, "quota_form_detected")
            return True

        previous = current

    snapshot(page, "quota_watch_timeout")
    return False

def field_context(page, c):
    parts = []
    try:
        cid = c.get_attribute("id")
        if cid:
            lab = page.locator(f'label[for="{cid}"]').first
            if lab.count(): parts.append(lab.inner_text())
    except Exception: pass
    for attr in ["aria-label", "placeholder", "name", "id", "title"]:
        try: parts.append(c.get_attribute(attr) or "")
        except Exception: pass
    for xp in ["xpath=..", "xpath=../.."]:
        try: parts.append(c.locator(xp).inner_text(timeout=500)[:500])
        except Exception: pass
    return " ".join(parts)

def find_fields(page, key):
    """
    Robust TTD field discovery.

    Priority:
    1. Exact TTD DOM name (input/select/textarea).
    2. Exact id/name/placeholder/aria-label.
    3. Existing label/context pattern fallback.
    """
    exact_names = {
        "name": "name",
        "age": "age",
        "gender": "gender",
        "id_type": "idType",
        "id_number": "idNumber",
        "email": "pilgrimEmail",
        "city": "pilgrimCity",
        "state": "pilgrimState",
        "country": "pilgrimCountry",
        "pincode": "pilgrimPincode",
    }

    result = []
    exact = exact_names.get(key)

    if exact:
        selectors = [
            f'[name="{exact}"]:visible',
            f'[id="{exact}"]:visible',
            f'[data-name="{exact}"]:visible',
            f'[data-field="{exact}"]:visible',
            f'input[placeholder*="{exact}" i]:visible',
            f'input[aria-label*="{exact}" i]:visible',
        ]
        seen = set()

        for selector in selectors:
            try:
                loc = page.locator(selector)
                for i in range(loc.count()):
                    c = loc.nth(i)
                    try:
                        sig = c.evaluate("(e)=>e.outerHTML.slice(0,500)")
                        if sig in seen:
                            continue
                        seen.add(sig)
                    except Exception:
                        pass
                    result.append(c)
            except Exception:
                pass

        if result:
            return result

    controls = page.locator(
        "input:visible, textarea:visible, select:visible, "
        "[contenteditable='true']:visible"
    )

    for i in range(controls.count()):
        c = controls.nth(i)
        try:
            if matches(field_context(page, c), FIELD_PATTERNS[key]):
                result.append(c)
        except Exception:
            pass

    return result

TTD_DROPDOWN_ITEM_SELECTOR = "li.floatingDropdown_listItem__tU_5x:visible"


def dropdown_options(page, control=None):
    """Return only the live TTD custom-dropdown items.

    The supplied live DOM uses:
        li.floatingDropdown_listItem__tU_5x

    When a control is supplied, scope the search to that control's wrapper so
    an old/stale dropdown from another pilgrim cannot be selected accidentally.
    """
    if control is not None:
        try:
            scoped = control.locator("xpath=..").locator(TTD_DROPDOWN_ITEM_SELECTOR)
            if scoped.count():
                return scoped
        except Exception:
            pass
    return page.locator(TTD_DROPDOWN_ITEM_SELECTOR)


def select_custom_dropdown(page, c, value):
    target = norm(str(value))
    aliases = {target}

    if target in {"male", "m"}:
        aliases.update({"male", "m"})
    elif target in {"female", "f"}:
        aliases.update({"female", "f"})
    elif target in {"transgender", "other"}:
        aliases.update({"transgender", "other"})
    elif target in {"aadhaar", "aadhar", "aadhaar card", "aadhar card"}:
        aliases.update({"aadhaar", "aadhar", "aadhaar card", "aadhar card"})

    try:
        c.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        c.click(force=True)
    except Exception:
        try:
            c.locator("xpath=..").click(force=True)
        except Exception:
            return False

    # TTD renders the list immediately after opening. Keep this short so the
    # automation does not feel slow, while allowing React to paint the list.
    page.wait_for_timeout(80)

    options = dropdown_options(page, c)
    count = options.count()
    print(f"[DEBUG] TTD dropdown option count: {count}")

    # Exact live TTD items only. This avoids accidentally seeing an option
    # belonging to another dropdown that remains visible in the DOM.
    for i in range(count):
        opt = options.nth(i)
        try:
            txt = norm(opt.inner_text())
        except Exception:
            continue

        print(f"[DEBUG] TTD option {i}: '{txt}'")
        if txt not in aliases:
            continue

        try:
            opt.scroll_into_view_if_needed()
            opt.click(force=True)
        except Exception:
            continue

        # React state/value update is normally immediate. One short check is
        # enough; avoid the previous 250ms + 300ms retry chain.
        page.wait_for_timeout(80)
        if verify(c, value):
            print(f"[OK] Exact TTD dropdown selection: {txt}")
            return True

        # One additional short check for asynchronous React state updates.
        page.wait_for_timeout(120)
        if verify(c, value):
            print(f"[OK] Exact TTD dropdown selection: {txt}")
            return True

    snapshot(page, f"{target.replace(' ', '_')}_dropdown_options_not_found")
    return False


def set_control(page, c, value, custom=False):
    if custom:
        return select_custom_dropdown(page, c, value)

    try:
        c.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        c.fill(str(value))
    except Exception:
        try:
            c.click(force=True)
            c.press("Control+A")
            c.type(str(value))
        except Exception:
            return False

    # Do not press Tab here. TTD's SPA can react to focus/keyboard events
    # and navigate away from the booking page. Playwright fill() already
    # triggers the input events required by the form.
    page.wait_for_timeout(150)
    return verify(c, value)

def verify(c, expected):
    wanted = norm(str(expected))

    # Standard input/select value.
    try:
        actual = norm(c.input_value())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    # Custom/readonly input exposing a value attribute.
    try:
        actual = norm(c.get_attribute("value") or "")
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    # Custom dropdown/control where selected value is represented as text.
    try:
        actual = norm(c.inner_text())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    return False

def fill_one(page, c, value, label="", custom=False):
    ok = set_control(page, c, value, custom=custom)

    if ok:
        print(f"[OK] {label}: verified")
    else:
        print(f"[MANUAL] {label}: verification failed")

    return ok

def fill_pilgrims(page, pilgrims, unresolved):
    for idx, p in enumerate(pilgrims, 1):
        print(f"[INFO] Pilgrim {idx}: {p.get('name', '')}")

        # Exact TTD DOM names: name, age, gender, idType, idNumber.
        ordered = [
            ("name", "Name", p.get("name")),
            ("age", "Age", p.get("age")),
            ("gender", "Gender", p.get("gender") or "Male"),
            ("id_type", "Photo ID Proof", p.get("id_type") or "Aadhaar Card"),
            ("id_number", "Photo ID Number", p.get("id_number")),
        ]

        for key, label, value in ordered:
            fields = find_fields(page, key)

            if len(fields) < idx:
                unresolved.append(
                    (f"Pilgrim {idx} - {label}",
                     "REDACTED" if key == "id_number" else value,
                     "not_found")
                )
                print(f"[MANUAL] Pilgrim {idx} - {label}: not found")
                continue

            field = fields[idx - 1]

            # TTD can leave idType/idNumber disabled until the preceding
            # dropdown selection completes.
            if key in ("id_type", "id_number"):
                try:
                    page.wait_for_timeout(150)
                    if field.is_disabled():
                        page.wait_for_timeout(300)
                except Exception:
                    pass

            if value in (None, ""):
                unresolved.append(
                    (f"Pilgrim {idx} - {label}",
                     "REDACTED" if key == "id_number" else "",
                     "not_configured")
                )
                print(f"[MANUAL] Pilgrim {idx} - {label}: not configured")
                continue

            if fill_one(page, field, value, f"Pilgrim {idx} - {label}", custom=(key in ("gender", "id_type"))):
                try:
                    current_url = page.url
                    print(f"[DEBUG] After {label}, URL: {current_url}")
                    if "/temples" in current_url:
                        print("[STOP] TTD navigated to /temples. Stopping field automation to preserve the session.")
                        return
                except Exception:
                    pass
            else:
                unresolved.append(
                    (f"Pilgrim {idx} - {label}",
                     "REDACTED" if key == "id_number" else value,
                     "verification_failed")
                )
                print(f"[MANUAL] Pilgrim {idx} - {label}: verification failed")

def fill_general(page, contact, unresolved):
    # Exact TTD DOM names from supplied HTML:
    # pilgrimEmail, pilgrimCity, pilgrimState, pilgrimCountry, pilgrimPincode.
    for key, label in [
        ("email", "Email"), ("city", "City"), ("state", "State"),
        ("country", "Country"), ("pincode", "Pincode")
    ]:
        value = contact.get(key)

        if not value or str(value).startswith("YOUR_"):
            unresolved.append((label, "", "not_configured"))
            print(f"[MANUAL] {label}: not configured")
            continue

        fields = find_fields(page, key)
        if not fields:
            unresolved.append((label, "", "not_found"))
            print(f"[MANUAL] {label}: not found")
            continue

        if fill_one(page, fields[0], value):
            print(f"[OK] {label}: verified")
        else:
            unresolved.append((label, value, "verification_failed"))
            print(f"[MANUAL] {label}: verification failed")


def save_auth_state(context):
    """Backup current cookies/localStorage without closing the browser."""
    try:
        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(TTD_AUTH_STATE_FILE))
        print(f"[AUTH] Saved auth state: {TTD_AUTH_STATE_FILE}")
    except Exception as exc:
        print(f"[WARN] Auth-state backup failed: {exc}")


def restore_auth_cookies(context):
    """Restore cookies from the optional auth-state backup."""
    if not TTD_AUTH_STATE_FILE.exists():
        print("[AUTH] No previous auth-state backup found.")
        return

    try:
        state = json.loads(TTD_AUTH_STATE_FILE.read_text(encoding="utf-8"))
        cookies = state.get("cookies", [])
        if cookies:
            context.add_cookies(cookies)
            print(f"[AUTH] Restored {len(cookies)} saved cookies.")
        else:
            print("[AUTH] Saved auth state contains no cookies.")
    except Exception as exc:
        print(f"[WARN] Could not restore saved auth cookies: {exc}")


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
    """Save state only when the page does not look like an OTP/login screen."""
    try:
        if not ttd_login_screen(page):
            save_auth_state(context)
    except Exception:
        pass


def hold_browser(page, reason, allow_resume=False):
    """Keep the browser open and let the user choose ready or CLOSE.

    ready means: continue/re-enter the automation from the current page.
    CLOSE is the only explicit command that ends the hold.
    """
    print("\n" + "=" * 72)
    print("[HOLD] Browser/session is being kept OPEN.")
    print(f"[HOLD] Reason: {reason}")
    try:
        print(f"[HOLD] Current URL: {page.url}")
    except Exception:
        pass
    print("[HOLD] You can manually take over the TTD browser now.")
    print("[HOLD] No reload, navigation, logout, or browser close will be performed.")
    print("[HOLD] Type ready after you have manually corrected/prepared the page.")
    print("[HOLD] Type CLOSE only when you explicitly want to end the script.")

    while True:
        command = input("[HOLD] Press ENTER / type READY to resume, or CLOSE to stop: ").strip().upper()

        # Blank ENTER is intentionally equivalent to READY.
        if command in ("", "READY"):
            print("[READY] Resuming automation from the CURRENT browser page.")
            return "READY"

        if command == "CLOSE":
            print("[CLOSE] Explicit CLOSE received.")
            return "CLOSE"

        print("[HOLD] Press ENTER, type READY, or type CLOSE.")

def main():
    data = load_data()
    booking = data.get("booking", {})

    with sync_playwright() as p:
        context = None
        page = None
        try:
            BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            print(f"[DEBUG] Browser profile: {BROWSER_PROFILE_DIR}")
            print(f"[DEBUG] Profile exists: {BROWSER_PROFILE_DIR.exists()}")

            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_DIR),
                headless=False,
                viewport={"width": 1440, "height": 950},
                timezone_id=booking.get("timezone", "Asia/Kolkata")
            )

            restore_auth_cookies(context)

            print(f"[DEBUG] Pages opened: {len(context.pages)}")
            page = context.pages[0] if context.pages else context.new_page()

            try:
                current_url = page.url
            except Exception:
                current_url = ""

            if not current_url.startswith("https://ttdevasthanams.ap.gov.in"):
                page.goto(TTD_URL, wait_until="domcontentloaded", timeout=60000)

            print(f"\n[INFO] Persistent browser profile: {BROWSER_PROFILE_DIR}")
            print(f"[INFO] Auth backup: {TTD_AUTH_STATE_FILE}")
            print("[INFO] The same Chromium profile is reused on every run.")

            page.wait_for_timeout(1500)

            if ttd_login_screen(page):
                print("[AUTH] TTD is asking for login/OTP.")
                print("[AUTH] Complete OTP/CAPTCHA manually.")
            else:
                print("[AUTH] Existing TTD session appears available.")
                print("[AUTH] No OTP should be needed unless TTD invalidates the server session.")

            print("\nBOOKING PREPARATION:")
            print("1. If TTD asks for OTP/CAPTCHA, complete it manually.")
            print("2. Open Special Entry Darshan.")
            print("3. Leave the browser on the quota/booking page.")
            print("4. Press ENTER (or type READY) when you want automation to start.")
            print("   You can manually correct anything at any point, then press ENTER or type READY again.")

            # Default behavior: simply pressing ENTER starts the automation.
            # Typing READY does exactly the same thing.
            while True:
                command = input("\nPress ENTER / type READY to start autofill, or CLOSE to exit: ").strip().upper()
                if command in ("", "READY"):
                    print("[READY] Starting automation.")
                    break
                if command == "CLOSE":
                    print("[CLOSE] Explicit CLOSE received.")
                    return
                print("[INFO] Press ENTER, type READY, or type CLOSE.")

            save_auth_if_authenticated(context, page)

            while True:
                # TTD booking-lock message: stop and let the user manually
                # resolve it. Never refresh or navigate automatically.
                if booking_in_progress(page):
                    print("\n[WARN] TTD reports: Pilgrims booking is already in progress.")
                    snapshot(page, "booking_already_in_progress")
                    save_auth_if_authenticated(context, page)

                    command = hold_browser(
                        page,
                        "TTD reports that a pilgrim booking is already in progress."
                    )
                    if command == "CLOSE":
                        return
                    continue

                # If the user is already on a form, do not run the watcher.
                state = page_state(page)
                if not state["form_signal"]:
                    if not watch_quota(page, int(booking.get("watch_timeout_minutes", 45))):
                        print("[WARN] Quota/form not detected.")
                        save_auth_if_authenticated(context, page)
                        command = hold_browser(
                            page,
                            "Quota/form was not detected. Manually prepare the TTD page, then type ready."
                        )
                        if command == "CLOSE":
                            return
                        continue

                # Check once more after watcher completion because TTD may have
                # displayed the booking-lock message during the wait.
                if booking_in_progress(page):
                    print("\n[WARN] TTD reports: Pilgrims booking is already in progress.")
                    snapshot(page, "booking_already_in_progress")
                    command = hold_browser(
                        page,
                        "TTD reports that a pilgrim booking is already in progress."
                    )
                    if command == "CLOSE":
                        return
                    continue

                snapshot(page, "live_booking_form")

                print("[INFO] Date and slot automation skipped for now.")
                print("[INFO] Staying on the current TTD booking page and filling fields.")
                page.wait_for_timeout(150)

                unresolved = []

                print("\n[AUTOFILL] Starting pilgrim/contact value entry...")
                fill_pilgrims(page, data.get("pilgrims", []), unresolved)
                fill_general(page, data.get("contact", {}), unresolved)

                fields = find_fields(page, "laddus")
                laddu_count = booking.get("laddoo_count", 0)
                if fields and fill_one(page, fields[0], laddu_count, "No. of Laddus"):
                    print("[OK] No. of Laddus: verified")
                else:
                    unresolved.append(("No. of Laddus", laddu_count, "not_found_or_verification_failed"))
                    print("[MANUAL] No. of Laddus")

                hundi = booking.get("hundi_offering", "0")
                if str(hundi) not in ("0", "0.0", ""):
                    fields = find_fields(page, "hundi")
                    if fields and fill_one(page, fields[0], hundi, "Hundi Offering"):
                        print("[OK] Hundi Offering: verified")
                    else:
                        unresolved.append(("Hundi Offering", hundi, "not_found_or_verification_failed"))

                save_auth_if_authenticated(context, page)
                snapshot(page, "after_autofill")

                if unresolved:
                    print("\nMANUAL FIELDS REQUIRED:")
                    for item in unresolved:
                        print(f"  - {item[0]}: {item[2]}")

                    command = hold_browser(
                        page,
                        "Some fields were unresolved. Correct them manually, then type ready to run the value-entry step again."
                    )
                    if command == "CLOSE":
                        return
                    continue

                print("\nFINAL REVIEW:")
                print("Verify date, slot, ticket count, every pilgrim, ID numbers,")
                print("contact details, laddu count and total cost.")
                print("\nIf you change/correct anything manually, press ENTER or type READY to run autofill again.")
                print("Payment remains manual and is never automated.")

                command = hold_browser(
                    page,
                    "Autofill completed. Manual review/payment remains under your control."
                )
                if command == "CLOSE":
                    return

                # ready from final review intentionally re-runs the configured
                # value-entry sequence on the SAME page and SAME browser session.
                continue

        except Exception as exc:
            print("\n[ERROR] Automation stopped due to an exception:")
            print(f"[ERROR] {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()

            if page is not None:
                try:
                    snapshot(page, "automation_exception")
                except Exception:
                    pass
                save_auth_if_authenticated(context, page)

                command = hold_browser(
                    page,
                    f"Unhandled exception: {type(exc).__name__}. Fix the page manually, then type ready to retry."
                )
                if command == "CLOSE":
                    return

                print("[ready] Browser remains open. The current script execution has stopped.")
                print("[ready] You can manually continue in the browser or restart the script.")
                while True:
                    command = input("[HOLD] Type CLOSE to end the script: ").strip().upper()
                    if command == "CLOSE":
                        return
            else:
                input("[HOLD] Browser was not fully initialized. Press ENTER to exit...")


if __name__ == "__main__":
    main()
