#!/usr/bin/env python3
"""
TTD Special Entry Darshan Booking Assistant v14 - HTML DOM mapped

Targets multiple supplied TTD Screen-1/Screen-2 DOM variants, including radio-card slots.

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
  - On the selected date, inspect all live slots immediately, including radio-card layouts.
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


def slot_card_candidates_fallback(page):
    """Discover slot cards across TTD Screen-1 variants.

    Supported variants include:
      1. Standard slots: ``Slot Time 8:00 am`` with a numeric button value.
      2. Older/Seva slots: a named seva followed by ``(11:00 AM)`` and a
         radio-style selection control.

    Availability count is authoritative for ranking.  Status colour is used
    only as a safety signal for clearly blocked cards.
    """
    candidates = []
    seen = set()

    time_pattern = re.compile(
        r'(?:slot\s*time\s+)?(\d{1,2}:\d{2}\s*[ap]m)', re.I
    )

    # Find visible text elements that contain a clock time. This covers both
    # "Slot Time 8:00 am" and named Seva cards such as
    # "Kalyana Kankanam (11:00 AM)".
    time_nodes = page.locator("*:visible").filter(has_text=time_pattern)

    for i in range(time_nodes.count()):
        node = time_nodes.nth(i)
        try:
            raw = norm(node.inner_text())
        except Exception:
            continue

        matches = list(time_pattern.finditer(raw))
        if not matches:
            continue

        # A parent can contain multiple cards. Use the first clock time only
        # when the element itself is reasonably compact; otherwise skip it and
        # let its child time element be discovered.
        m = matches[0]
        slot_time = normalize_time_text(m.group(1))
        if not slot_time:
            continue

        card = None
        for level in range(1, 9):
            try:
                ancestor = node.locator("xpath=" + "/.." * level)
                if not ancestor.count():
                    continue
                txt = norm(ancestor.inner_text())
                if not time_pattern.search(txt):
                    continue

                controls = ancestor.locator(
                    "button:visible, input[type='radio']:visible, "
                    "input[type='button']:visible, [role='radio']:visible"
                )
                if not controls.count():
                    continue

                # A genuine slot card should also expose availability or quota
                # text. This prevents the large page-level container from
                # becoming the selected card.
                if not re.search(
                    r'\bavailable\b|quota\s+is\s+full|quota\s+not\s+released|'
                    r'slot\s+not\s+available',
                    txt,
                    re.I,
                ):
                    continue

                card = ancestor
                # Stop at the first compact card containing one clock time.
                # If this ancestor contains multiple distinct times, keep
                # walking until a child-sized card is found.
                distinct_times = {
                    normalize_time_text(x.group(1))
                    for x in time_pattern.finditer(txt)
                }
                if len(distinct_times) <= 1:
                    break
            except Exception:
                continue

        if card is None:
            continue

        try:
            txt = norm(card.inner_text())
        except Exception:
            continue

        # Avoid duplicate discovery from nested visible elements.
        card_times = {
            normalize_time_text(x.group(1))
            for x in time_pattern.finditer(txt)
        }
        if len(card_times) != 1 or slot_time not in card_times:
            continue

        # Determine whether this is the standard unnamed slot form or the
        # older named-Seva form.
        is_named_seva = not bool(re.search(r'\bslot\s*time\b', txt, re.I))

        control = None
        try:
            buttons = card.locator("button:visible")
            for bi in range(buttons.count()):
                b = buttons.nth(bi)
                val = (b.get_attribute("value") or "").strip()
                if re.fullmatch(r"\d{4}", val):
                    control = b
                    break
            if control is None and buttons.count():
                control = buttons.last
        except Exception:
            pass

        if control is None:
            try:
                radios = card.locator("input[type='radio']:visible")
                if radios.count():
                    control = radios.first
            except Exception:
                pass

        if control is None:
            try:
                role_radios = card.locator('[role="radio"]:visible')
                if role_radios.count():
                    control = role_radios.first
            except Exception:
                pass

        if control is None:
            continue

        availability_text = ""
        availability_color = ""
        try:
            block = card.locator(
                "[class*='SlotBooking_availableSlotSection']:visible"
            ).first
            if block.count():
                availability_text = norm(block.inner_text())
                try:
                    availability_color = norm(
                        block.evaluate("e => getComputedStyle(e).backgroundColor")
                    )
                except Exception:
                    pass
        except Exception:
            pass

        if not availability_text:
            # Older Seva markup does not necessarily have the newer generated
            # SlotBooking class. The card text itself contains e.g. "47 Available".
            availability_text = txt

        state_lower = availability_text.lower()
        count = availability_count(availability_text or txt)

        blocked_colors = {
            "rgb(232, 72, 60)",
            "rgb(255, 30, 34)",
            "rgb(255, 31, 38)",
            "rgb(121, 192, 235)",
            "rgb(5, 175, 232)",
            "rgb(204, 204, 204)",
        }

        blocked = (
            "quota is full" in state_lower
            or "quota not released" in state_lower
            or "slot not available" in state_lower
            or availability_color in blocked_colors
            or count == 0
        )

        is_available = (
            count is not None and count > 0
        ) or (
            "available" in state_lower
            and "not available" not in state_lower
            and "not released" not in state_lower
            and "quota is full" not in state_lower
        )

        # Seva cards explicitly state whether the card is for 1 or 2 persons.
        # When that information exists, preserve it so choose_booking_slot can
        # avoid selecting a 1-person Seva for a 2-pilgrim booking.
        capacity = None
        cap_match = re.search(
            r'\b(\d+)\s*persons?\b',
            txt,
            re.I,
        )
        if cap_match:
            try:
                capacity = int(cap_match.group(1))
            except ValueError:
                capacity = None

        if is_named_seva and capacity is not None:
            # The actual ticket count is checked later in choose_booking_slot.
            # Keep the card discoverable here.
            pass

        try:
            cursor = norm(card.evaluate("e => getComputedStyle(e).cursor"))
        except Exception:
            cursor = ""
        if cursor == "not-allowed":
            blocked = True
            is_available = False

        item = {
            "locator": card,
            "button": control,
            "text": txt,
            "time": slot_time,
            "minutes": parse_slot_time(slot_time),
            "availability_text": availability_text,
            "availability_color": availability_color,
            "availability_count": count,
            "capacity": capacity,
            "named_seva": is_named_seva,
            "available": bool(is_available and not blocked),
            "blocked": blocked,
        }

        # If the same time is discovered multiple times, prefer the smallest
        # card / the one with a numeric availability count.
        key = slot_time
        if key not in seen:
            seen.add(key)
            candidates.append(item)
        else:
            for pos, existing in enumerate(candidates):
                if existing["time"] == key:
                    if (
                        existing.get("availability_count") is None
                        and count is not None
                    ):
                        candidates[pos] = item
                    break


    # ------------------------------------------------------------------
    # RADIO-CARD FALLBACK
    # ------------------------------------------------------------------
    # Some TTD Screen-1 variants use a radio input inside each slot card,
    # e.g.:
    #
    #   [radio] 3079 Available   12:00 PM
    #   [radio] 2390 Available    1:00 PM
    #
    # These cards do not always expose the newer SlotBooking_* classes.
    # Discover them directly from the radio control and walk upward to the
    # smallest ancestor containing exactly one time + availability count.
    # This is a fallback/merge path, so the existing button/card layouts
    # continue to work unchanged.
    try:
        radio_inputs = page.locator(
            "input[type='radio']:visible, [role='radio']:visible"
        )

        for ri in range(radio_inputs.count()):
            radio = radio_inputs.nth(ri)

            try:
                checked = bool(radio.is_checked()) if radio.get_attribute("type") == "radio" else False
            except Exception:
                checked = False

            radio_card = None
            radio_text = ""

            for level in range(1, 9):
                try:
                    ancestor = radio.locator("xpath=" + "/.." * level)
                    if not ancestor.count():
                        continue

                    txt = norm(ancestor.inner_text())
                    times = list(time_pattern.finditer(txt))
                    if len(times) != 1:
                        continue

                    if not re.search(
                        r"\bavailable\b|\bremaining\b|\bquota\b",
                        txt,
                        re.I,
                    ):
                        continue

                    # Keep searching for a smaller valid card. The first
                    # matching ancestor is normally the actual radio card.
                    radio_card = ancestor
                    radio_text = txt
                    break
                except Exception:
                    continue

            if radio_card is None:
                continue

            tm = time_pattern.search(radio_text)
            if not tm:
                continue

            slot_time = normalize_time_text(tm.group(1))
            count = availability_count(radio_text)

            if count is None:
                # A radio card without a numeric availability cannot be
                # ranked safely under the requested highest-availability rule.
                continue

            lower = radio_text.lower()
            blocked = (
                count <= 0
                or "quota is full" in lower
                or "quota not released" in lower
                or "slot not available" in lower
            )

            try:
                cursor = norm(
                    radio_card.evaluate("e => getComputedStyle(e).cursor")
                )
                if cursor == "not-allowed":
                    blocked = True
            except Exception:
                pass

            # Determine whether the card explicitly states a person
            # capacity (used by Seva-style variants).
            capacity = None
            cap_match = re.search(r"\b(\d+)\s*persons?\b", radio_text, re.I)
            if cap_match:
                try:
                    capacity = int(cap_match.group(1))
                except ValueError:
                    capacity = None

            # Radio layouts are standard slots unless they contain an
            # explicit named-Seva marker and capacity.
            named_seva = not bool(
                re.search(r"\bslot\s*time\b", radio_text, re.I)
            )

            item = {
                "locator": radio_card,
                "button": radio,
                "text": radio_text,
                "time": slot_time,
                "minutes": parse_slot_time(slot_time),
                "availability_text": radio_text,
                "availability_color": "",
                "availability_count": count,
                "capacity": capacity,
                "named_seva": named_seva,
                "available": bool(count > 0 and not blocked),
                "blocked": blocked,
            }

            # Merge by time. Prefer the radio-discovered item when the
            # existing discovery has no numeric availability.
            replaced = False
            for pos, existing in enumerate(candidates):
                if existing.get("time") == slot_time:
                    replaced = True
                    if (
                        existing.get("availability_count") is None
                        and count is not None
                    ):
                        candidates[pos] = item
                    break

            if not replaced:
                candidates.append(item)
                seen.add(slot_time)

    except Exception as exc:
        print(
            f"[DEBUG] Radio-slot fallback inspection failed: "
            f"{type(exc).__name__}: {exc}"
        )

    return candidates



def slot_card_candidates(page):
    """Fast Screen-1 slot scan using one browser-side DOM evaluation.

    The previous implementation walked many visible DOM nodes through
    Playwright and then walked ancestors for each node. That is robust but can
    be expensive on a large React page. This fast path instead:
      1. Finds likely slot controls directly (radio/role=radio/4-digit buttons).
      2. Walks only a few DOM ancestors inside the browser process.
      3. Extracts time, availability and person capacity in one evaluate().
      4. Adds a temporary stable attribute to each discovered control so Python
         only has to locate/click the winning control.

    If the fast DOM scan finds nothing, the original compatibility scanner is
    used automatically.
    """
    marker = "data-ttd-fast-slot-id"
    try:
        raw_items = page.evaluate("""(marker) => {
            const timeRe = /(?:slot\\s*time\\s+)?(\\d{1,2}:\\d{2}\\s*[ap]m)\\b/i;
            const availRe = /\\b(\\d[\\d,]*)\\s*(?:available|remaining)\\b/i;
            const availRe2 = /(?:available|availability|quota)\\s*[:\\-]?\\s*(\\d[\\d,]*)/i;
            const capRe = /\\b(\\d+)\\s*persons?\\b/i;
            const blockedRe = /quota\\s+is\\s+full|quota\\s+not\\s+released|slot\\s+not\\s+available/i;
            const blockedColors = new Set([
                'rgb(232, 72, 60)', 'rgb(255, 30, 34)', 'rgb(255, 31, 38)',
                'rgb(121, 192, 235)', 'rgb(5, 175, 232)', 'rgb(204, 204, 204)'
            ]);

            const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                return r.width > 0 && r.height > 0 &&
                       st.display !== 'none' && st.visibility !== 'hidden';
            };

            const norm = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
            const timeToMinutes = (t) => {
                const m = String(t || '').match(/(\\d{1,2}):(\\d{2})\\s*([ap]m)/i);
                if (!m) return null;
                let h = Number(m[1]);
                const min = Number(m[2]);
                const ap = m[3].toLowerCase();
                if (ap === 'pm' && h !== 12) h += 12;
                if (ap === 'am' && h === 12) h = 0;
                return h * 60 + min;
            };

            const controls = Array.from(document.querySelectorAll(
                'input[type="radio"], [role="radio"], button, input[type="button"], input[type="submit"]'
            )).filter(visible);

            const out = [];
            let seq = 0;

            for (const control of controls) {
                const value = norm(control.getAttribute('value'));
                const aria = norm(control.getAttribute('aria-label'));
                const ownText = norm(control.innerText || control.textContent || '');
                const likelyButton = /^\\d{4}$/.test(value);
                const likelyRadio = control.matches('input[type="radio"], [role="radio"]');
                if (!likelyRadio && !likelyButton && !timeRe.test(ownText + ' ' + aria)) continue;

                let card = null;
                let el = control;
                for (let level = 0; level < 7 && el; level++, el = el.parentElement) {
                    const txt = norm(el.innerText || el.textContent || '');
                    const tm = txt.match(timeRe);
                    if (!tm) continue;
                    if (!/\\bavailable\\b|quota\\s+is\\s+full|quota\\s+not\\s+released|slot\\s+not\\s+available/i.test(txt)) continue;
                    const allTimes = [...txt.matchAll(/(?:slot\\s*time\\s+)?(\\d{1,2}:\\d{2}\\s*[ap]m)\\b/ig)]
                        .map(x => x[1].replace(/\\s+/g, ' ').trim().toLowerCase());
                    const distinct = [...new Set(allTimes)];
                    if (distinct.length === 1) { card = el; break; }
                }
                if (!card) continue;

                const text = norm(card.innerText || card.textContent || '');
                const tm = text.match(timeRe);
                if (!tm) continue;
                const slotTime = tm[1].replace(/\\s+/g, ' ').trim().toLowerCase();
                let m = text.match(availRe);
                if (!m) m = text.match(availRe2);
                const count = m ? Number(m[1].replace(/,/g, '')) : null;
                const cap = text.match(capRe);
                const capacity = cap ? Number(cap[1]) : null;

                const disabled = !!control.disabled ||
                    control.getAttribute('aria-disabled') === 'true' ||
                    card.getAttribute('aria-disabled') === 'true';
                const state = text.toLowerCase();
                let color = '';
                try { color = getComputedStyle(card).backgroundColor || ''; } catch (_) {}
                const blocked = disabled || blockedRe.test(state) || blockedColors.has(color) || count === 0;
                const available = count !== null && count > 0 && !blocked;
                if (!available && count === null && !/\\bavailable\\b/i.test(state)) continue;

                const id = `ttd-fast-${Date.now()}-${seq++}`;
                control.setAttribute(marker, id);
                out.push({
                    id,
                    time: slotTime,
                    minutes: timeToMinutes(slotTime),
                    availability_count: Number.isFinite(count) ? count : null,
                    capacity: Number.isFinite(capacity) ? capacity : null,
                    named_seva: !/\\bslot\\s*time\\b/i.test(text),
                    available,
                    blocked,
                    availability_text: text,
                    text
                });
            }

            // Deduplicate by time, preferring the record with numeric
            // availability. This handles nested radio/button controls.
            const byTime = new Map();
            for (const item of out) {
                const prev = byTime.get(item.time);
                if (!prev || (prev.availability_count == null && item.availability_count != null)) {
                    byTime.set(item.time, item);
                }
            }
            return [...byTime.values()];
        }""", marker)
        if not raw_items:
            return slot_card_candidates_fallback(page)

        candidates=[]
        for item in raw_items:
            control=page.locator(f'[{marker}="{item["id"]}"]:visible').first
            if not control.count():
                continue
            item["button"] = control
            item["locator"] = control
            candidates.append(item)

        if candidates:
            return candidates
    except Exception as exc:
        print(f"[DEBUG] Fast slot scan failed: {type(exc).__name__}: {exc}")

    return slot_card_candidates_fallback(page)



def click_slot(page, slot):
    """Click a TTD slot using its actual selection control or card."""
    control = slot.get("button")
    card = slot["locator"]
    try:
        if control is not None and control.count():
            control.scroll_into_view_if_needed()
            control.click(force=True, timeout=1500)
        else:
            card.scroll_into_view_if_needed()
            card.click(force=True, timeout=1500)
        print(f"[OK] Slot click sent: {slot['time']}")
        return True
    except Exception as exc:
        # Some TTD versions attach the handler to the card rather than the
        # radio/button. Try the card as a compatibility fallback.
        try:
            card.click(force=True, timeout=1500)
            print(f"[OK] Slot card click sent: {slot['time']}")
            return True
        except Exception:
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
    """Set Number of Tickets across current/older TTD Screen-1 markup."""
    ticket_count = int(ticket_count)
    if ticket_count < 1:
        print("[TICKETS] No pilgrims configured; cannot set ticket count.")
        return False

    wanted = str(ticket_count)
    field = None

    # Newer markup may expose a stable name.
    try:
        named = page.locator('input[name="noOfTickets"]:visible')
        if named.count():
            field = named.first
    except Exception:
        pass

    # Older/current TTD markup shown in the live page has no useful name on the
    # input. Find the visible input whose ancestor text contains "Number of
    # Tickets". This avoids confusing it with pilgrim Age/ID inputs.
    if field is None:
        inputs = page.locator("input:visible")
        for i in range(inputs.count()):
            candidate = inputs.nth(i)
            try:
                sig = candidate.evaluate("e => e.outerHTML")
                if "type=\"hidden\"" in sig.lower():
                    continue
            except Exception:
                pass
            for level in range(1, 6):
                try:
                    anc = candidate.locator("xpath=" + "/.." * level)
                    if not anc.count():
                        continue
                    txt = norm(anc.inner_text())
                    if "number of tickets" in txt:
                        field = candidate
                        break
                except Exception:
                    continue
            if field is not None:
                break

    if field is None:
        print("[TICKETS] Number of Tickets control was not found.")
        snapshot(page, "ticket_control_not_found")
        return False

    try:
        field.scroll_into_view_if_needed()
        field.click(force=True, timeout=1500)
    except Exception as exc:
        print(f"[TICKETS] Could not open Number of Tickets: {type(exc).__name__}: {exc}")
        return False

    # TTD has used li, role=option, buttons, and plain divs for this menu.
    candidates = []
    seen = set()
    selectors = [
        "li:visible",
        '[role="option"]:visible',
        "button:visible",
        "div:visible",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(loc.count()):
                item = loc.nth(i)
                try:
                    label = norm(item.inner_text())
                except Exception:
                    continue
                if label not in {wanted, wanted.zfill(2)}:
                    continue
                # Ignore large containers whose text happens to contain the
                # number. Exact single-value text is required.
                if label in seen:
                    continue
                seen.add(label)
                candidates.append(item)
        except Exception:
            pass

    if not candidates:
        print(f"[TICKETS] Ticket option {wanted} was not visible after opening the control.")
        snapshot(page, "ticket_options_not_detected")
        return False

    # Prefer the smallest element whose text is exactly the requested number.
    option = candidates[0]
    try:
        option.scroll_into_view_if_needed()
        option.click(force=True, timeout=1500)
        print(f"[OK] Number of Tickets selected: {wanted}")
        return True
    except Exception as exc:
        print(f"[TICKETS] Ticket option click failed: {type(exc).__name__}: {exc}")
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


def choose_booking_slot(page, booking, ticket_count=None):
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

    # For older/named Seva cards, TTD explicitly exposes the supported person
    # count (for example "1 Person" or "2 Persons"). Do not select a Seva
    # that cannot accommodate the configured pilgrim count. Standard
    # "Slot Time ..." cards do not use this capacity rule.
    requested_tickets = int(ticket_count or 0)
    available = []
    for item in slots:
        if not item["available"]:
            continue
        if (
            requested_tickets > 0
            and item.get("named_seva")
            and item.get("capacity") is not None
            and item["capacity"] < requested_tickets
        ):
            print(
                f"[SLOT] Skipping {item['time']} - capacity "
                f"{item['capacity']} < tickets {requested_tickets}"
            )
            continue
        available.append(item)

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

    # TTD renders slot availability based on selected ticket count in some versions.
    print(f"[TICKETS] Total pilgrims in config: {ticket_count}")
    if not set_ttd_ticket_count(page, ticket_count):
        print("[TICKETS] Could not set Number of Tickets safely.")
        return False

    print(
        f"[SLOT] Checking available slots for "
        f"{selected_date.strftime('%d/%m/%Y')} after setting tickets..."
    )
    ok, selected_slot = choose_booking_slot(page, booking, ticket_count)
    if not ok:
        print("[SLOT] No selectable/available slot was found in the current live UI.")
        return False

    print(
        f"[OK] Available slot selected: {selected_slot['time']} "
        f"for {selected_date.strftime('%d/%m/%Y')}"
    )

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

def _find_labeled_control(page, label):
    """Find visible form controls by their own field-label wrapper.

    Older TTD builds are less consistent about the `name` attribute. In those
    builds a broad field-context search can confuse Age with Photo ID Number
    because both controls live inside the same Pilgrim Details row.

    We therefore inspect the DOM hierarchy for the *smallest* ancestor that:
      - contains the exact field label, and
      - contains only a small number of form controls.

    This keeps Age attached to the Age input, Name to Name, etc., even when
    the old form has weak/missing semantic attributes.
    """
    wanted = norm(label)
    controls = page.locator(
        "input:visible, textarea:visible, select:visible, "
        "[role='combobox']:visible, [contenteditable='true']:visible"
    )
    found = []

    for i in range(controls.count()):
        c = controls.nth(i)
        try:
            matched = c.evaluate(
                """
                (el, wanted) => {
                    const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    let node = el;
                    let best = null;
                    let bestScore = -1;
                    for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                        const text = norm(node.innerText || node.textContent || '');
                        if (!text.includes(wanted)) continue;

                        const fields = node.querySelectorAll(
                            'input, textarea, select, [role="combobox"], [contenteditable="true"]'
                        );
                        const fieldCount = fields.length;

                        // A field wrapper normally contains one control. Allow
                        // up to three for older custom TTD wrappers.
                        if (fieldCount < 1 || fieldCount > 3) continue;

                        // Prefer the deepest/smallest matching wrapper.
                        const score = (100 - depth * 10) - fieldCount;
                        if (score > bestScore) {
                            best = true;
                            bestScore = score;
                        }
                    }
                    return !!best;
                }
                """,
                wanted,
            )
            if matched:
                found.append(c)
        except Exception:
            pass

    return found


def _find_pilgrim_fields_by_label(page, key):
    labels = {
        "name": "name",
        "age": "age",
        "gender": "gender",
        "id_type": "photo id proof",
        "id_number": "photo id number",
    }
    label = labels.get(key)
    if not label:
        return []
    return _find_labeled_control(page, label)


def find_fields(page, key):
    """Find TTD fields across old and new Screen-2 implementations.

    Strategy for pilgrim fields:
      1. Exact semantic DOM names from either known TTD form.
      2. Exact field-label wrapper matching for older/weak DOM builds.
      3. Very narrow attribute fallback.

    We deliberately do NOT use broad ancestor text matching for pilgrim
    fields. That was the source of the old-form bug where Photo ID Number
    (e.g. 441...) could be selected as Age.
    """
    name_aliases = {
        "name": ["fname", "name"],
        "age": ["age"],
        "gender": ["gender", "sex"],
        "id_type": ["photoIdType", "idType", "photoIDType", "photo_id_type"],
        "id_number": ["idProofNumber", "idNumber", "photoIdNumber", "photoIDNumber", "photo_id_number"],
        "email": ["pilgrimEmail", "email"],
        "city": ["pilgrimCity", "city"],
        "state": ["pilgrimState", "state"],
        "country": ["pilgrimCountry", "country"],
        "pincode": ["pilgrimPincode", "pincode", "pinCode", "postalCode"],
    }

    aliases = name_aliases.get(key, [])

    # Exact semantic names first.
    for exact in aliases:
        selectors = [
            f'[name="{exact}"]:visible',
            f'[data-name="{exact}"]:visible',
            f'[data-field="{exact}"]:visible',
        ]
        if key in {"email", "city", "state", "country", "pincode"}:
            selectors.append(f'[id="{exact}"]:visible')

        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = loc.count()
                if count:
                    return [loc.nth(i) for i in range(count)]
            except Exception:
                pass

    # Critical compatibility layer for the older TTD form.
    if key in {"name", "age", "gender", "id_type", "id_number"}:
        labeled = _find_pilgrim_fields_by_label(page, key)
        if labeled:
            return labeled

    # Narrow attribute fallback only; never inspect broad parent text for age or
    # ID fields.
    result = []
    controls = page.locator(
        "input:visible, textarea:visible, select:visible, "
        "[role='combobox']:visible, [contenteditable='true']:visible"
    )
    field_attr_patterns = {
        "name": re.compile(r"^(fname|name)$", re.I),
        "age": re.compile(r"^age$", re.I),
        "gender": re.compile(r"^(gender|sex)$", re.I),
        "id_type": re.compile(r"^(photoidtype|idtype|photo[_-]?id[_-]?type)$", re.I),
        "id_number": re.compile(r"^(idproofnumber|idnumber|photoidnumber|photo[_-]?id[_-]?number)$", re.I),
    }

    pattern = field_attr_patterns.get(key)
    if pattern:
        for i in range(controls.count()):
            c = controls.nth(i)
            try:
                attrs = " ".join(
                    (c.get_attribute(a) or "")
                    for a in ("name", "placeholder", "aria-label", "title", "label")
                )
                if pattern.search(norm(attrs)):
                    result.append(c)
            except Exception:
                pass
        return result

    for i in range(controls.count()):
        c = controls.nth(i)
        try:
            if matches(field_context(page, c), FIELD_PATTERNS[key]):
                result.append(c)
        except Exception:
            pass
    return result

def _visible_dropdown_options(page, control=None):
    """Find dropdown options across old and new TTD dropdown implementations."""
    selectors = [
        "li.floatingDropdown_listItem__tU_5x:visible",  # newer observed
        "li[class*='floatingDropdown_listItem']:visible",
        "[role='option']:visible",
        "li:visible",
    ]

    # Prefer options attached to the control's immediate wrapper. If that
    # wrapper has no options, TTD may render the menu in a portal; then search
    # the page for visible option-like elements.
    for selector in selectors:
        try:
            if control is not None:
                scoped = control.locator("xpath=..").locator(selector)
                if scoped.count():
                    return scoped
        except Exception:
            pass

    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count():
                return loc
        except Exception:
            pass

    return page.locator("li:visible")


def _dropdown_value_matches(control, expected):
    """Check a custom dropdown using value, text, aria/value attributes."""
    wanted = norm(str(expected))
    for attr in ("value", "aria-label", "data-value", "title"):
        try:
            actual = norm(control.get_attribute(attr) or "")
            if actual == wanted or wanted in actual:
                return True
        except Exception:
            pass
    try:
        actual = norm(control.input_value())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass
    try:
        actual = norm(control.inner_text())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass
    return False


def select_custom_dropdown(page, c, value):
    """Select Gender/Photo ID for both old and new TTD dropdowns."""
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

    # Re-check the exact control immediately before clicking. React may have
    # replaced the input after the previous field was populated.
    try:
        c.click(force=True, timeout=1500)
    except Exception:
        try:
            c.locator("xpath=..").click(force=True, timeout=1500)
        except Exception:
            return False

    page.wait_for_timeout(100)

    options = _visible_dropdown_options(page, c)
    count = options.count()
    print(f"[DEBUG] TTD dropdown option count: {count}")

    # Search exact visible text first. This avoids selecting an unrelated
    # option such as a hidden/stale dropdown belonging to another row.
    exact_matches = []
    seen_option_keys = set()
    for i in range(count):
        opt = options.nth(i)
        try:
            if not opt.is_visible():
                continue
            txt = norm(opt.inner_text())
        except Exception:
            continue
        if txt in aliases:
            try:
                key = opt.evaluate("e => e.outerHTML.slice(0,1200)")
            except Exception:
                key = f"option:{i}:{txt}"
            if key not in seen_option_keys:
                seen_option_keys.add(key)
                exact_matches.append(opt)

    # Older TTD builds can render dropdown choices in a portal using div/span
    # rather than li or role=option. Use Playwright's text engine as a final
    # option-discovery fallback.
    if not exact_matches:
        for alias in aliases:
            try:
                text_matches = page.get_by_text(alias, exact=True)
                for i in range(text_matches.count()):
                    opt = text_matches.nth(i)
                    if opt.is_visible():
                        exact_matches.append(opt)
            except Exception:
                pass

    print(f"[DEBUG] Exact dropdown options matching requested value: {len(exact_matches)}")

    for opt in exact_matches:
        try:
            print(f"[DEBUG] Selecting TTD dropdown option: '{norm(opt.inner_text())}'")
            opt.scroll_into_view_if_needed()
            opt.click(force=True, timeout=1500)
            page.wait_for_timeout(100)
        except Exception:
            continue

        if _dropdown_value_matches(c, value):
            print(f"[OK] TTD dropdown selection verified: {value}")
            return True

        # React can replace the control after selection. Re-find the same
        # semantic field and check whether the selected row now contains value.
        try:
            key = None
            nm = norm(c.get_attribute("name") or "")
            if nm in {"gender", "sex"}:
                key = "gender"
            elif nm in {"photoidtype", "idtype", "photo_id_type", "photoidtype"}:
                key = "id_type"
            else:
                # Old TTD controls may have no useful name at all. Infer the
                # semantic field from its visible label/wrapper.
                for candidate_key, candidate_label in (("gender", "gender"), ("id_type", "photo id proof")):
                    try:
                        wrapper_text = norm(c.locator("xpath=..").inner_text())
                        if candidate_label in wrapper_text:
                            key = candidate_key
                            break
                    except Exception:
                        pass
            if key:
                fresh = find_fields(page, key)
                for fresh_control in fresh:
                    if _dropdown_value_matches(fresh_control, value):
                        print(f"[OK] TTD dropdown selection verified after React refresh: {value}")
                        return True
        except Exception:
            pass

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

    return verify(c, value)


def verify(c, expected):
    wanted = norm(str(expected))

    try:
        actual = norm(c.input_value())
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

    try:
        actual = norm(c.get_attribute("value") or "")
        if actual == wanted or wanted in actual:
            return True
    except Exception:
        pass

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
    """Populate pilgrims safely on both old and new TTD Screen-2 forms."""
    if not isinstance(pilgrims, list):
        return

    total = len(pilgrims)
    print(f"[PILGRIMS] Configured pilgrim count: {total}")

    # Detect the schema once from the currently visible DOM. This prevents a
    # React re-render from making one field use the new names and another field
    # use the old fallback mapping during the same row.
    schema = {
        "name": "fname" if page.locator('[name="fname"]:visible').count() else "name",
        "age": "age",
        "gender": "gender" if page.locator('[name="gender"]:visible').count() else "sex",
        "id_type": "photoIdType" if page.locator('[name="photoIdType"]:visible').count() else "idType",
        "id_number": "idProofNumber" if page.locator('[name="idProofNumber"]:visible').count() else "idNumber",
    }
    print(f"[PILGRIMS] Detected TTD field schema: {schema}")

    for idx, p in enumerate(pilgrims, 1):
        print(f"[INFO] Pilgrim {idx}/{total}: {p.get('name', '')}")

        ordered = [
            ("name", "Name", p.get("name")),
            ("age", "Age", p.get("age")),
            ("gender", "Gender", p.get("gender") or "Male"),
            ("id_type", "Photo ID Proof", p.get("id_type") or "Aadhaar Card"),
            ("id_number", "Photo ID Number", p.get("id_number")),
        ]

        for key, label, value in ordered:
            # Use the schema-specific selector first, then the alias-aware
            # finder. Always take the nth occurrence for pilgrim idx.
            fields = find_fields(page, key)
            print(
                f"[PILGRIMS] Pilgrim {idx} {label}: "
                f"found {len(fields)} matching visible field(s)"
            )

            if len(fields) < idx:
                unresolved.append((
                    f"Pilgrim {idx} - {label}",
                    "REDACTED" if key == "id_number" else value,
                    "not_found",
                ))
                print(
                    f"[MANUAL] Pilgrim {idx} - {label}: "
                    f"not_found (found {len(fields)}, need row {idx})"
                )
                continue

            field = fields[idx - 1]

            if value in (None, ""):
                unresolved.append((
                    f"Pilgrim {idx} - {label}",
                    "REDACTED" if key == "id_number" else "",
                    "not_configured",
                ))
                print(f"[MANUAL] Pilgrim {idx} - {label}: not configured")
                continue

            if key in ("id_type", "id_number"):
                try:
                    if field.is_disabled():
                        unresolved.append((
                            f"Pilgrim {idx} - {label}",
                            "REDACTED" if key == "id_number" else value,
                            "disabled",
                        ))
                        print(f"[MANUAL] Pilgrim {idx} - {label}: field is disabled")
                        continue
                except Exception:
                    pass

            ok = fill_one(
                page,
                field,
                value,
                f"Pilgrim {idx} - {label}",
                custom=(key in ("gender", "id_type")),
            )

            if not ok:
                unresolved.append((
                    f"Pilgrim {idx} - {label}",
                    "REDACTED" if key == "id_number" else value,
                    "verification_failed",
                ))
                continue

            try:
                if "/temples" in page.url:
                    print("[STOP] TTD navigated to /temples. Stopping field automation to preserve the session.")
                    return
            except Exception:
                pass

    print(f"[OK] Processed all {total} configured pilgrim row(s).")

def fill_general(page, contact, unresolved):
    """Populate the optional Generic/General Details section.

    TTD Screen 2 can appear in two forms:
      1. Pilgrim Details only.
      2. Pilgrim Details + Generic/General Details.

    Generic details are OPTIONAL from the automation-flow perspective:
    if none of the generic fields are present on the current Screen 2,
    skip the section and continue with the pilgrim fields.

    If the generic section is present, populate the fields that are both
    present and configured. Missing configured generic fields are reported
    for manual attention, but the entire section is not treated as mandatory
    when TTD does not render it.
    """
    generic_fields = {
        key: find_fields(page, key)
        for key in ("email", "city", "state", "country", "pincode")
    }

    present_keys = [key for key, fields in generic_fields.items() if fields]

    if not present_keys:
        print("[GENERAL] Generic/General Details form not present; skipping it.")
        return

    print(
        "[GENERAL] Generic/General Details form detected; "
        f"available fields: {', '.join(present_keys)}"
    )

    for key, label in [
        ("email", "Email"), ("city", "City"), ("state", "State"),
        ("country", "Country"), ("pincode", "Pincode")
    ]:
        fields = generic_fields[key]

        # Field is not rendered on this version of Screen 2.
        # Do not fail the whole flow because the section may be partial.
        if not fields:
            print(f"[GENERAL] {label}: field not present; skipping.")
            continue

        value = contact.get(key)

        if not value or str(value).startswith("YOUR_"):
            unresolved.append((label, "", "not_configured"))
            print(f"[MANUAL] {label}: not configured")
            continue

        if fill_one(page, fields[0], value):
            print(f"[OK] {label}: verified")
        else:
            unresolved.append((label, value, "verification_failed"))
            print(f"[MANUAL] {label}: verification failed")


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
        print(f"[AUTH] Saved auth backup: {TTD_AUTH_STATE_FILE}")
    except Exception as exc:
        print(f"[WARN] Auth-state backup failed: {exc}")


def restore_auth_cookies(context):
    """Deprecated: do not inject stale cookies into a persistent TTD profile.

    launch_persistent_context() already reuses the profile directory, including
    the site's normal browser storage. Injecting cookies from a previous
    snapshot can create a mixed/stale authentication state and trigger another
    OTP. This function is retained only for compatibility with older code.
    """
    print("[AUTH] Persistent profile is authoritative; skipping stale cookie restore.")


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
    print("[AUTH] Authenticated persistent profile saved. Future runs will reuse it.")
    return True

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
    """Screen 2: populate pilgrim rows, optionally populate Generic Details, then Continue."""
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

            # Screen 2 is complete after Pilgrim Details are populated.
            # Generic/General Details are filled only when that form is present.
            # Then click Continue once.
            if not click_ttd_continue(page, 1, "Screen 2"):
                print("[CONTINUE] Screen 2 Continue failed.")
                snapshot(page, "screen2_continue_failed")
                hold_result = hold_for_manual_takeover(
                    page,
                    "Screen 2 fields are populated, but Continue could not be clicked."
                )
                if hold_result in ("1", "2", "CLOSE"):
                    return hold_result
                continue

            save_auth_if_authenticated(page.context, page)
            print("[OK] Screen 2 Continue clicked.")
            print("[EXECUTION 2] Browser remains open for final manual review.")
            snapshot(page, "screen2_continue_complete")
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
                if not wait_for_auth_completion(page, context):
                    print("[AUTH] Authentication is incomplete; browser will remain open.")
                    return
            else:
                print("[AUTH] Existing TTD session appears available from the persistent profile.")
                print("[AUTH] No cookie injection or session reconstruction will be performed.")

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
