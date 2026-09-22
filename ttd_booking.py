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
  - On the selected date, inspect all live slots immediately.
  - Slot selection prioritizes the slot with the HIGHEST reported availability;
    preferred_slot is only a tie-breaker, then earliest slot time.
  - No artificial waiting is performed for Screen 1, Screen 2, slots, or Continue.
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
from datetime import datetime, date, timedelta
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


# ---------------------------------------------------------------------------
# TTD DATE / SLOT SELECTION
# ---------------------------------------------------------------------------
# The supplied live TTD HTML shows that calendar cells expose their state via
# inline CSS.  We use that only as a secondary signal; cursor/pointer-events,
# the calendar month heading, and the actual slot text are also checked.
#
# Known TTD colors from the supplied HTML:
#   rgb(255, 191, 0) = Filling Fast
#   rgb(255, 30, 34)  = Quota is Full
#   rgb(5, 175, 232)  = Quota Not Released
#   rgb(204, 204, 204) = Slot Not Available
#   rgb(94, 184, 18) / rgb(121, 193, 57) = selectable/available green
# ---------------------------------------------------------------------------

TTD_DATE_SELECTABLE_COLORS = {
    "rgb(255, 191, 0)",   # Filling Fast
    "rgb(94, 184, 18)",   # observed green date state
    "rgb(121, 193, 57)",  # observed green availability state
}
TTD_DATE_BLOCKED_COLORS = {
    "rgb(255, 30, 34)",   # Quota is Full
    "rgb(5, 175, 232)",    # Quota Not Released
    "rgb(204, 204, 204)",  # Slot Not Available
}


def parse_target_date(value):
    """Accept common config formats and return a date object."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(
        f"Invalid booking.target_date {value!r}. Use DD/MM/YYYY, e.g. 29/09/2026."
    )


def normalize_time_text(value):
    """Normalize 8 AM / 08:00 AM / 08:00AM into a comparable form."""
    text = norm(str(value or ""))
    text = text.replace(".", ":")
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b", text, re.I)
    if not m:
        return text
    hour = int(m.group(1))
    minute = int(m.group(2) or "00")
    ampm = m.group(3).lower()
    return f"{hour:02d}:{minute:02d} {ampm}"


def parse_slot_time(value):
    """Return minutes since midnight for sorting/comparison."""
    normalized = normalize_time_text(value)
    m = re.fullmatch(r"(\d{2}):(\d{2})\s*([ap]m)", normalized)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour == 12:
        hour = 0
    if m.group(3) == "pm":
        hour += 12
    return hour * 60 + minute


def calendar_month_heading_to_date(text):
    """Parse headings such as 'September 2026'."""
    text = norm(text)
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(text.title(), fmt).date().replace(day=1)
        except ValueError:
            pass
    return None


def calendar_cell_state(td):
    """Read the state of a TTD calendar <td>."""
    try:
        style = norm(td.get_attribute("style") or "")
    except Exception:
        style = ""
    try:
        bg = norm(td.evaluate("e => getComputedStyle(e).backgroundColor"))
    except Exception:
        bg = ""
    try:
        cursor = norm(td.evaluate("e => getComputedStyle(e).cursor"))
    except Exception:
        cursor = ""
    try:
        pointer_events = norm(td.evaluate("e => getComputedStyle(e).pointerEvents"))
    except Exception:
        pointer_events = ""

    combined = f"{style} {bg}"
    blocked = (
        cursor == "not-allowed"
        or pointer_events == "none"
        or any(color in combined for color in TTD_DATE_BLOCKED_COLORS)
    )
    selectable = (
        not blocked
        and (cursor in ("pointer", "auto", "") or pointer_events in ("auto", "", "visible"))
        and (
            any(color in combined for color in TTD_DATE_SELECTABLE_COLORS)
            or cursor == "pointer"
        )
    )
    return {
        "style": style,
        "background": bg,
        "cursor": cursor,
        "pointer_events": pointer_events,
        "blocked": blocked,
        "selectable": selectable,
    }


def calendar_date_candidates(page):
    """Return calendar date cells with a resolved calendar month/year."""
    candidates = []
    months = page.locator(".DesktopCalender_month__Jj3MW:visible")

    for mi in range(months.count()):
        month = months.nth(mi)
        try:
            heading_loc = month.locator("div").filter(has_text=re.compile(r"^[A-Za-z]+\s+20\d{2}$"))
            heading = ""
            for hi in range(min(heading_loc.count(), 10)):
                txt = norm(heading_loc.nth(hi).inner_text())
                if calendar_month_heading_to_date(txt):
                    heading = txt
                    break
            month_start = calendar_month_heading_to_date(heading)
            if not month_start:
                continue

            cells = month.locator("td[id]")
            for ci in range(cells.count()):
                td = cells.nth(ci)
                cid = td.get_attribute("id") or ""
                m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", cid.strip())
                if not m:
                    continue
                day_num = int(m.group(1))
                month_index = int(m.group(2))

                # IMPORTANT: TTD's calendar cell id uses JavaScript-style
                # zero-based month indexing. For example: September = 8,
                # November = 10, December = 11. The visible calendar
                # heading is authoritative for the actual month/year.
                # Therefore December 2026's "10/11" means 10-Dec-2026.
                actual_month = month_index + 1
                if actual_month != month_start.month:
                    # Ignore previous/next-month overflow cells shown in
                    # the same calendar grid.
                    continue
                try:
                    cell_date = date(month_start.year, actual_month, day_num)
                except ValueError:
                    continue

                state = calendar_cell_state(td)
                candidates.append({
                    "date": cell_date,
                    "locator": td,
                    "id": cid,
                    **state,
                })
        except Exception as exc:
            print(f"[DEBUG] Calendar month {mi} inspection failed: {type(exc).__name__}: {exc}")

    if candidates:
        grouped = {}
        for item in candidates:
            grouped.setdefault(item["date"].strftime("%m/%Y"), 0)
            grouped[item["date"].strftime("%m/%Y")] += 1
        print(f"[DEBUG] Calendar dates resolved from visible month headings: {grouped}")

    return candidates


def selected_date_signal(page, target):
    """Check the TTD page text for the selected calendar date."""
    wanted_variants = {
        target.strftime("%d/%m/%Y"),
        target.strftime("%-d/%-m/%Y") if hasattr(target, 'strftime') else "",
        target.strftime("%d %b,%Y"),
        target.strftime("%-d %b,%Y") if hasattr(target, 'strftime') else "",
    }
    body = body_text(page)
    normalized_body = norm(body)
    return any(norm(v) and norm(v) in normalized_body for v in wanted_variants)


def click_calendar_date(page, candidate):
    """Click one calendar cell and verify the page reacted."""
    td = candidate["locator"]
    before_url = page.url
    before_body = body_text(page)
    try:
        td.scroll_into_view_if_needed()
        td.click(force=True)
        page.wait_for_timeout(600)
    except Exception as exc:
        print(f"[DEBUG] Calendar click failed for {candidate['id']}: {type(exc).__name__}: {exc}")
        return False

    # TTD normally updates the slot section without changing URL.
    if page.url != before_url:
        print(f"[DEBUG] Calendar click changed URL: {before_url} -> {page.url}")
    after_body = body_text(page)
    changed = after_body != before_body
    return changed or selected_date_signal(page, candidate["date"])


def wait_for_target_date(page, target, timeout_minutes):
    """Wait for the exact target date to become selectable without reloading."""
    timeout_minutes = max(0.0, float(timeout_minutes or 0))
    if timeout_minutes <= 0:
        return None

    deadline = time.time() + timeout_minutes * 60.0
    print(f"[DATE] Target date is present but not selectable yet; watching for up to {timeout_minutes:g} minutes.")
    print("[DATE] No reload/navigation will be performed while waiting.")
    last_state = None

    while time.time() < deadline:
        candidates = calendar_date_candidates(page)
        for item in candidates:
            if item["date"] != target:
                continue

            state_key = (item["background"], item["cursor"], item["pointer_events"], item["selectable"])
            if state_key != last_state:
                print(
                    f"[DATE] Target {item['id']}: cursor={item['cursor']}, "
                    f"pointer-events={item['pointer_events']}, bg={item['background']}"
                )
                last_state = state_key

            if item["selectable"]:
                print("[DATE] Target date has become selectable.")
                return item

        remaining = max(0, int(deadline - time.time()))
        print(f"[DATE] Target date still unavailable; waiting... ({remaining}s remaining)")
        page.wait_for_timeout(2000)

    print("[DATE] Target-date availability watch timed out.")
    return None


def choose_booking_date(page, booking):
    """
    Select target_date or, with NEXT_AVAILABLE, the earliest selectable date
    currently exposed by the visible TTD calendar.

    Returns (True, selected_date) on success, (False, None) on failure.
    """
    target = parse_target_date(booking.get("target_date"))
    date_fallback = norm(booking.get("date_fallback", "NONE")).upper()

    print(f"[DATE] Requested target date: {target.strftime('%d/%m/%Y')}")

    candidates = calendar_date_candidates(page)
    if not candidates:
        print("[DATE] No readable TTD calendar date cells were found.")
        snapshot(page, "date_calendar_not_detected")
        return False, None

    # Deduplicate by date while keeping the first live locator.
    by_date = {}
    for item in candidates:
        by_date.setdefault(item["date"], item)

    requested = by_date.get(target)
    if requested:
        print(
            f"[DATE] Target {requested['id']}: "
            f"cursor={requested['cursor']}, pointer-events={requested['pointer_events']}, "
            f"bg={requested['background']}"
        )
        if requested["selectable"]:
            if click_calendar_date(page, requested):
                print(f"[OK] Selected target date: {target.strftime('%d/%m/%Y')}")
                return True, target
            print("[DATE] Target date looked selectable but click verification failed.")
        else:
            print("[DATE] Target date is not selectable in the live UI.")

            # TTD commonly shows future dates in blue while quota is not yet
            # released. Optionally wait for the exact requested date to turn
            # green/yellow and become clickable, without reloading the page.
            # Fail fast: do not wait for an unreleased target date.
            print("[DATE] Target date is not selectable now; failing fast.")

    else:
        print("[DATE] Target date is not exposed by the currently visible calendar.")

    if date_fallback != "NEXT_AVAILABLE":
        return False, None

    # NEXT_AVAILABLE means the earliest date that TTD currently exposes as
    # selectable in the visible calendar. It is not restricted to dates after
    # the requested target. This allows an earlier released date such as 28/09
    # to be selected when the requested date is still unreleased.
    available = [
        item for item in by_date.values()
        if item["selectable"]
    ]
    available.sort(key=lambda x: x["date"])

    if available:
        print(
            "[DATE] NEXT_AVAILABLE candidates: "
            + ", ".join(item["date"].strftime("%d/%m/%Y") for item in available)
        )

    for item in available:
        print(f"[DATE] Trying next available date: {item['date'].strftime('%d/%m/%Y')}")
        if click_calendar_date(page, item):
            print(f"[OK] Selected NEXT_AVAILABLE date: {item['date'].strftime('%d/%m/%Y')}")
            return True, item["date"]

    print("[DATE] No selectable date was safely found in the visible calendar.")
    snapshot(page, "date_selection_failed")
    return False, None


def slot_card_candidates(page):
    """
    Discover the actual TTD slot cards from #scrollSlotType.

    The live TTD DOM exposes each slot as an outer div with cursor:pointer.
    Each card contains an availability block, seva name/time, ticket count,
    price, and a radio button.
    """
    root = page.locator("#scrollSlotType:visible")
    if not root.count():
        return []

    time_re = re.compile(r"\(\s*(\d{1,2}:\d{2}\s*[AP]M)\s*\)", re.I)
    cards = root.locator("div[style*='cursor: pointer']")

    results = []
    seen = set()

    for i in range(cards.count()):
        card = cards.nth(i)

        try:
            txt = norm(card.inner_text())
        except Exception:
            continue

        time_match = time_re.search(txt)
        if not time_match:
            continue

        slot_time = normalize_time_text(time_match.group(1))

        available_text = ""
        availability_color = ""

        try:
            blocks = card.locator("div")
            for bi in range(min(blocks.count(), 12)):
                block = blocks.nth(bi)
                btxt = norm(block.inner_text())

                if re.search(
                    r"\bavailable\b|\bfilling\s+fast\b|\bquota\s+is\s+full\b|"
                    r"\bquota\s+not\s+released\b|\bslot\s+not\s+available\b",
                    btxt,
                    re.I,
                ):
                    available_text = btxt
                    try:
                        availability_color = norm(
                            block.evaluate("e => getComputedStyle(e).backgroundColor")
                        )
                    except Exception:
                        pass
                    break
        except Exception:
            pass

        state_lower = available_text.lower()

        blocked = (
            "quota is full" in state_lower
            or "quota not released" in state_lower
            or "slot not available" in state_lower
            or availability_color in {
                "rgb(255, 30, 34)",
                "rgb(5, 175, 232)",
                "rgb(204, 204, 204)",
            }
        )

        is_available = (
            (
                "available" in state_lower
                and "not available" not in state_lower
                and "not released" not in state_lower
                and "quota is full" not in state_lower
            )
            or availability_color in {
                "rgb(121, 193, 57)",
                "rgb(255, 191, 0)",
                "rgb(94, 184, 18)",
            }
        )

        try:
            cursor = norm(card.evaluate("e => getComputedStyle(e).cursor"))
        except Exception:
            cursor = ""

        if cursor == "not-allowed":
            blocked = True
            is_available = False

        button = card.locator("button")
        if button.count() == 0:
            continue

        try:
            key = card.evaluate(
                "e => e.outerHTML.replace(/\\s+/g, ' ').slice(0, 1800)"
            )
        except Exception:
            key = f"{i}:{slot_time}:{txt[:250]}"

        if key in seen:
            continue
        seen.add(key)

        results.append({
            "locator": card,
            "text": txt,
            "time": slot_time,
            "minutes": parse_slot_time(slot_time),
            "availability_text": available_text,
            "availability_color": availability_color,
            "availability_count": availability_count(available_text or txt),
            "available": bool(is_available and not blocked),
            "blocked": blocked,
        })

    unique = {}
    for item in results:
        key = (item["time"], item["text"][:300])
        unique.setdefault(key, item)

    return list(unique.values())

def click_slot(page, slot):
    """Click the selected TTD slot immediately; do not verify afterward."""
    card = slot["locator"]
    try:
        button = card.locator("button").last
        if button.count():
            button.click(force=True, timeout=1000)
        else:
            card.click(force=True, timeout=1000)
        print(f"[OK] Slot click sent immediately: {slot['time']}")
        return True
    except Exception as exc:
        print(
            f"[SLOT] Slot click failed for {slot['time']}: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def total_pilgrim_count(data):
    """Return the number of pilgrims configured in pilgrims.json."""
    pilgrims = data.get("pilgrims", [])
    if isinstance(pilgrims, list):
        return len(pilgrims)

    # Tolerate alternate shapes from older config versions.
    for key in ("pilgrim_details", "pilgrimDetails", "passengers", "travellers"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)

    return 0


def set_ttd_ticket_count(page, ticket_count):
    """Set TTD ticket count with no post-click verification."""
    ticket_count = int(ticket_count)
    if ticket_count < 1:
        print("[TICKETS] No pilgrims configured; cannot set ticket count.")
        return False

    field = page.locator('input[name="noOfTickets"]:visible').first
    if not field.count():
        print("[TICKETS] TTD number-of-tickets field was not found; failing fast.")
        return False

    wanted = str(ticket_count)
    print(f"[TICKETS] Selecting {ticket_count} ticket(s) immediately.")

    try:
        field.click(force=True, timeout=1000)
    except Exception as exc:
        print(f"[TICKETS] Could not open ticket dropdown: {type(exc).__name__}: {exc}")
        return False

    # Prefer the actual TTD custom-dropdown option. No sleep and no verification.
    selectors = [
        "li.floatingDropdown_listItem__tU_5x:visible",
        '[role="option"]:visible',
    ]
    candidates = []
    seen = set()

    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(loc.count()):
                item = loc.nth(i)
                try:
                    label = norm(item.inner_text())
                except Exception:
                    continue
                if label and label not in seen:
                    seen.add(label)
                    candidates.append((label, item))
        except Exception:
            pass

    exact = [
        pair for pair in candidates
        if pair[0] in (wanted, wanted.zfill(2))
    ]
    exact.sort(key=lambda pair: (0 if pair[0] == wanted.zfill(2) else 1))

    if not exact:
        print(
            f"[TICKETS] Ticket option {wanted}/{wanted.zfill(2)} not visible; "
            "failing fast."
        )
        return False

    option_label, option = exact[0]
    try:
        option.click(force=True, timeout=1000)
        print(f"[OK] Ticket option {option_label!r} clicked immediately.")
        return True
    except Exception as exc:
        print(
            f"[TICKETS] Ticket option click failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def availability_count(text):
    """Extract a numeric availability count when TTD exposes one."""
    text = norm(text)
    patterns = [
        r'(?:available|availability|quota)\s*[:\-]?\s*(\d+)',
        r'(\d+)\s*(?:tickets?|seats?|spots?)?\s*(?:available|remaining)',
        r'(\d+)\s*(?:available|remaining)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def slot_priority(item, preferred=""):
    """
    Highest availability is the PRIMARY priority.
    Preferred slot is only a tie-breaker, followed by earliest time.
    Unknown numeric availability is ranked below known availability counts.
    """
    count = item.get("availability_count")
    known = count is not None
    preferred_match = bool(preferred and item.get("time") == preferred)
    return (
        1 if known else 0,
        count if known else -1,
        1 if preferred_match else 0,
        -(item.get("minutes") if item.get("minutes") is not None else 9999),
    )


def choose_booking_slot(page, booking):
    """
    Select the slot with the HIGHEST reported availability.

    Priority:
      1. Highest numeric availability.
      2. Preferred slot, only when availability is tied/unknown.
      3. Earliest slot time.

    No waiting/polling is performed.
    """
    preferred = normalize_time_text(booking.get("preferred_slot", ""))
    slots = slot_card_candidates(page)

    if not slots:
        print("[SLOT] No TTD slot cards were detected; failing fast.")
        snapshot(page, "slot_cards_not_detected")
        return False, None

    available = [item for item in slots if item["available"]]
    print("[SLOT] Live slot inventory:")
    for item in sorted(
        slots,
        key=lambda x: (x["minutes"] is None, x["minutes"] or 9999)
    ):
        print(
            f"  - {item['time']} | available={item['available']} | "
            f"availability_count={item.get('availability_count')} | "
            f"blocked={item['blocked']} | state={item['availability_text']!r}"
        )

    if not available:
        print("[SLOT] No available slot is exposed right now; failing fast.")
        snapshot(page, "no_available_slot")
        return False, None

    ranked = sorted(
        available,
        key=lambda item: slot_priority(item, preferred),
        reverse=True,
    )

    selected = ranked[0]
    print(
        f"[SLOT] Highest-availability candidate: {selected['time']} | "
        f"availability_count={selected.get('availability_count')} | "
        f"preferred={selected['time'] == preferred}"
    )

    if click_slot(page, selected):
        print(f"[OK] Selected highest-availability slot: {selected['time']}")
        return True, selected

    print("[SLOT] Highest-availability slot click failed; failing fast.")
    snapshot(page, "highest_availability_slot_click_failed")
    return False, None


def visible_continue_buttons(page):
    """Return visible buttons/controls whose visible text is exactly Continue."""
    results = []
    seen = set()

    selectors = [
        "button:visible",
        'input[type="button"]:visible',
        'input[type="submit"]:visible',
        '[role="button"]:visible',
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(loc.count()):
                el = loc.nth(i)
                try:
                    label = norm(el.inner_text())
                except Exception:
                    try:
                        label = norm(el.get_attribute("value") or "")
                    except Exception:
                        label = ""

                if label.lower() != "continue":
                    continue

                try:
                    sig = el.evaluate("(e)=>e.outerHTML.slice(0,800)")
                except Exception:
                    sig = f"{selector}:{i}"

                if sig not in seen:
                    seen.add(sig)
                    results.append(el)
        except Exception:
            pass

    return results


def click_ttd_continue(page, count=1, stage=""):
    """Attempt Continue immediately; do not verify success or wait for navigation."""
    count = int(count)
    if count < 1:
        return True

    for step in range(1, count + 1):
        buttons = visible_continue_buttons(page)
        if not buttons:
            print(
                f"[CONTINUE] {stage or 'TTD'}: Continue not visible; "
                "no wait/retry. Check manually in the browser."
            )
            return False

        try:
            buttons[-1].click(force=True, timeout=1000)
            print(f"[CONTINUE] Click attempt sent ({step}/{count}); no verification.")
        except Exception as exc:
            print(
                f"[CONTINUE] Click attempt failed ({step}/{count}): "
                f"{type(exc).__name__}: {exc}"
            )
            return False

    return True


def select_date_and_slot(page, booking, ticket_count):
    """
    Screen 1 flow:
      1. Select requested/fallback date.
      2. Load the slot inventory and select a genuinely available slot.
      3. Set Number of Tickets = total pilgrims.
      4. Click Continue once to move to Screen 2.
    """
    if not booking.get("target_date"):
        print("[DATE] booking.target_date is not configured; manual selection required.")
        return False

    ok, selected_date = choose_booking_date(page, booking)
    if not ok:
        return False

    print(
        f"[SLOT] Checking available slots for "
        f"{selected_date.strftime('%d/%m/%Y')} before setting tickets..."
    )

    # IMPORTANT: slot availability is checked/selected FIRST.
    # Fail fast: inspect the current live DOM once; do not wait/poll.
    ok, selected_slot = choose_booking_slot(page, booking)

    if not ok:
        print("[SLOT] No selectable/available slot was found in the current live UI.")
        return False

    print(
        f"[OK] Available slot selected: {selected_slot['time']} "
        f"for {selected_date.strftime('%d/%m/%Y')}"
    )

    # Only after a valid slot is selected, set ticket count.
    print(f"[TICKETS] Total pilgrims in config: {ticket_count}")
    if not set_ttd_ticket_count(page, ticket_count):
        print("[TICKETS] Could not set Number of Tickets safely.")
        return False

    print(
        f"[OK] Screen 1 fields complete: "
        f"date={selected_date.strftime('%d/%m/%Y')}, "
        f"slot={selected_slot['time']}, tickets={ticket_count}"
    )

    # Continue from Screen 1 -> Screen 2.
    if not click_ttd_continue(page, 1, "Screen 1"):
        print("[CONTINUE] Screen 1 Continue failed.")
        return False

    print("[OK] Screen 1 Continue clicked; TTD should now be on Screen 2.")
    return True



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
        # IMPORTANT: do not deduplicate repeated pilgrim controls by outerHTML.
        # TTD renders rows with identical markup, so Pilgrim 1 and Pilgrim 2 can
        # have exactly the same outerHTML. Deduplicating by markup incorrectly
        # collapses all repeated rows into the first row.
        #
        # Prefer the first selector that actually finds controls. This preserves
        # every DOM occurrence in document order: nth(0) = pilgrim 1, nth(1) =
        # pilgrim 2, etc.
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = loc.count()
                if count:
                    return [loc.nth(i) for i in range(count)]
            except Exception:
                pass

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
        if verify(c, value):
            print(f"[OK] Exact TTD dropdown selection: {txt}")
            return True

        # One additional short check for asynchronous React state updates.
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
    """Populate every configured pilgrim row in the TTD form.

    The screenshot shows the same five fields repeated horizontally for each
    pilgrim. TTD renders those controls as repeated DOM elements, so the nth
    matching field belongs to pilgrim n. We discover all visible fields for each
    column once, then populate row-by-row using the same index across Name, Age,
    Gender, Photo ID Proof and Photo ID Number.

    This intentionally does not wait for additional rows to appear. If the
    currently rendered form does not contain enough rows, it fails fast and
    reports exactly which pilgrim/field is missing.
    """
    if not isinstance(pilgrims, list):
        return

    field_cache = {}
    for key in ("name", "age", "gender", "id_type", "id_number"):
        field_cache[key] = find_fields(page, key)
        print(f"[PILGRIMS] {key}: found {len(field_cache[key])} visible field(s) for {len(pilgrims)} configured pilgrim(s).")

    for idx, p in enumerate(pilgrims, 1):
        print(f"[INFO] Pilgrim {idx}/{len(pilgrims)}: {p.get('name', '')}")

        ordered = [
            ("name", "Name", p.get("name")),
            ("age", "Age", p.get("age")),
            ("gender", "Gender", p.get("gender") or "Male"),
            ("id_type", "Photo ID Proof", p.get("id_type") or "Aadhaar Card"),
            ("id_number", "Photo ID Number", p.get("id_number")),
        ]

        for key, label, value in ordered:
            fields = field_cache[key]

            if len(fields) < idx:
                unresolved.append(
                    (f"Pilgrim {idx} - {label}",
                     "REDACTED" if key == "id_number" else value,
                     "not_found")
                )
                print(
                    f"[MANUAL] Pilgrim {idx} - {label}: row {idx} is not currently "
                    f"rendered (found {len(fields)} row(s))."
                )
                continue

            field = fields[idx - 1]

            # Fail fast if the ID controls are disabled right now. Do not wait
            # for a React re-render.
            if key in ("id_type", "id_number"):
                try:
                    if field.is_disabled():
                        unresolved.append(
                            (f"Pilgrim {idx} - {label}",
                             "REDACTED" if key == "id_number" else value,
                             "disabled")
                        )
                        print(f"[MANUAL] Pilgrim {idx} - {label}: field is disabled now.")
                        continue
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

            ok = fill_one(
                page,
                field,
                value,
                f"Pilgrim {idx} - {label}",
                custom=(key in ("gender", "id_type")),
            )

            if not ok:
                unresolved.append(
                    (f"Pilgrim {idx} - {label}",
                     "REDACTED" if key == "id_number" else value,
                     "verification_failed")
                )
                continue

            try:
                if "/temples" in page.url:
                    print("[STOP] TTD navigated to /temples. Stopping field automation to preserve the session.")
                    return
            except Exception:
                pass

    print(f"[OK] Processed all {len(pilgrims)} configured pilgrim row(s).")

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


def wait_for_execution_mode():
    while True:
        command = input(
            "\nSelect execution mode (independent):\n"
            "  1 = Populate Screen 1 ONLY: Date + Number of Tickets + Slot\n"
            "  2 = Populate Screen 2 ONLY: Pilgrim Details\n"
            "  CLOSE = Exit\n"
            "Choice: "
        ).strip().upper()
        if command in ("1", "2"):
            return command
        if command == "CLOSE":
            return "CLOSE"
        print("[INFO] Enter 1, 2, or CLOSE.")


def hold_for_manual_takeover(page, reason):
    """Keep browser open; 1/2 can start either independent execution."""
    print("\n" + "=" * 72)
    print("[HOLD] Browser/session is being kept OPEN.")
    print(f"[HOLD] Reason: {reason}")
    try:
        print(f"[HOLD] Current URL: {page.url}")
    except Exception:
        pass
    print("[HOLD] No reload, logout, or browser close will be performed.")
    print("[HOLD] 1 = run Screen 1 (Date + Tickets + Slot)")
    print("[HOLD] 2 = run Screen 2 (Pilgrim Details)")
    print("[HOLD] ENTER / READY = retry the CURRENT execution")
    print("[HOLD] CLOSE = exit the script.")

    while True:
        command = input("[HOLD] Choice: ").strip().upper()
        if command in ("", "READY"):
            return "RETRY"
        if command in ("1", "2"):
            return command
        if command == "CLOSE":
            return "CLOSE"
        print("[HOLD] Enter 1, 2, ENTER/READY, or CLOSE.")



def run_execution_1(page, booking, ticket_count):
    """Screen 1 only: date + total ticket count + slot."""
    print("\n[EXECUTION 1] Screen 1: Date + Tickets + Slot")
    print(f"[EXECUTION 1] Total pilgrims/tickets: {ticket_count}")

    while True:
        try:
            if booking_in_progress(page):
                snapshot(page, "execution1_booking_in_progress")
                hold_result = hold_for_manual_takeover(
                    page, "TTD reports that a pilgrim booking is already in progress."
                )
                if hold_result in ("1", "2", "CLOSE"):
                    return hold_result

            ok = select_date_and_slot(page, booking, ticket_count)
            if ok:
                save_auth_if_authenticated(page.context, page)
                print("[EXECUTION 1] Date, ticket count, and slot completed.")
                print("[EXECUTION 1] Browser remains open.")
                return True

            snapshot(page, "execution1_date_slot_failed")
            hold_result = hold_for_manual_takeover(
                page,
                "Date/ticket/slot selection could not be completed safely. "
                "Correct Screen 1 manually, then retry."
            )
            if hold_result in ("1", "2", "CLOSE"):
                return hold_result

        except Exception as exc:
            print(f"\n[EXECUTION 1 ERROR] {type(exc).__name__}: {exc}")
            snapshot(page, "execution1_exception")
            save_auth_if_authenticated(page.context, page)
            hold_result = hold_for_manual_takeover(
                page,
                f"Execution 1 exception: {type(exc).__name__}. Browser remains open."
            )
            if hold_result in ("1", "2", "CLOSE"):
                return hold_result


def run_execution_2(page, data):
    """Screen 2 only: populate all configured pilgrim rows + contact details."""
    print("\n[EXECUTION 2] Screen 2: Pilgrim Details")

    pilgrims = data.get("pilgrims", [])
    contact = data.get("contact", {})
    if not isinstance(pilgrims, list) or not pilgrims:
        return hold_for_manual_takeover(
            page, "No pilgrim records were found in pilgrims.json."
        )

    while True:
        try:
            unresolved = []
            print(f"[EXECUTION 2] Filling {len(pilgrims)} pilgrim(s).")
            fill_pilgrims(page, pilgrims, unresolved)
            fill_general(page, contact, unresolved)
            snapshot(page, "execution2_pilgrim_details")

            if unresolved:
                print(f"[EXECUTION 2] {len(unresolved)} field(s) need manual attention.")
                for item in unresolved:
                    print(f"[MANUAL] {item[0]} -> {item[2]}")
                hold_result = hold_for_manual_takeover(
                    page,
                    "Some pilgrim/contact fields were not verified. "
                    "Correct them manually, then retry Execution 2."
                )
                if hold_result in ("1", "2", "CLOSE"):
                    return hold_result
                continue

            print("[EXECUTION 2] Pilgrim/contact details populated successfully.")

            # User requested two Continue clicks from Screen 2.
            if not click_ttd_continue(page, 2, "Screen 2"):
                print("[CONTINUE] Screen 2 Continue sequence failed.")
                snapshot(page, "screen2_continue_failed")
                hold_result = hold_for_manual_takeover(
                    page,
                    "Screen 2 fields are populated, but one or both Continue clicks "
                    "could not be verified."
                )
                if hold_result in ("1", "2", "CLOSE"):
                    return hold_result
                continue

            save_auth_if_authenticated(page.context, page)
            print("[OK] Screen 2 Continue clicked twice.")
            print("[EXECUTION 2] Browser remains open for final manual review.")
            snapshot(page, "screen2_continue_twice_complete")
            return True

        except Exception as exc:
            print(f"\n[EXECUTION 2 ERROR] {type(exc).__name__}: {exc}")
            snapshot(page, "execution2_exception")
            save_auth_if_authenticated(page.context, page)
            hold_result = hold_for_manual_takeover(
                page,
                f"Execution 2 exception: {type(exc).__name__}. Browser remains open."
            )
            if hold_result in ("1", "2", "CLOSE"):
                return hold_result



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

            if ttd_login_screen(page):
                print("[AUTH] TTD is asking for login/OTP.")
                print("[AUTH] Complete OTP/CAPTCHA manually.")
            else:
                print("[AUTH] Existing TTD session appears available.")
                print("[AUTH] No OTP should be needed unless TTD invalidates the server session.")

            print("\nBOOKING PREPARATION:")
            print("1. If TTD asks for OTP/CAPTCHA, complete it manually.")
            print("2. You can run Screen 1 and Screen 2 independently at any time.")
            print("3. The browser/session stays open unless you explicitly choose CLOSE.")

            save_auth_if_authenticated(context, page)

            ticket_count = total_pilgrim_count(data)
            if ticket_count < 1:
                print("[WARN] No pilgrims configured; Screen 1 cannot set ticket count.")
            else:
                print(f"[INFO] Total pilgrims configured: {ticket_count}")
                print(f"[INFO] Screen 1 will set Number of Tickets = {ticket_count}")

            while True:
                mode = wait_for_execution_mode()
                if mode == "CLOSE":
                    print("[CLOSE] Explicit CLOSE received.")
                    return

                # Use the currently visible/latest tab after manual navigation.
                if context.pages:
                    page = context.pages[-1]

                if mode == "1":
                    result = run_execution_1(page, booking, ticket_count)
                else:
                    result = run_execution_2(page, data)

                # 1 or 2 can be selected from ANY hold/error point.
                while result in ("1", "2"):
                    if context.pages:
                        page = context.pages[-1]
                    if result == "1":
                        result = run_execution_1(page, booking, ticket_count)
                    else:
                        result = run_execution_2(page, data)

                if result == "CLOSE":
                    print("[CLOSE] Explicit CLOSE received.")
                    return

                print("\n[MENU] Execution completed/paused.")
                print("[MENU] Browser remains OPEN.")
                print("[MENU] Choose 1 or 2 for the next independent execution.")
                print("[MENU] You may manually move to either TTD screen.")
                print("[MENU] Choose 1 or 2 again whenever you want.")

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
