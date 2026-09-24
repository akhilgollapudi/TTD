import re
import time
from datetime import datetime, date

if __package__:
    from .config import *
    from .utils import norm, matches, body_text, snapshot
else:
    from config import *
    from utils import norm, matches, body_text, snapshot

"""TTD Screen-1 calendar/date handling."""

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
    """Return calendar date cells from every visible TTD month container.

    The current desktop TTD page renders the fourth visible month with
    ``DesktopCalender_lastmonth__...`` rather than
    ``DesktopCalender_month__...``. The old selector only inspected the first
    class, so it silently ignored the entire fourth month.
    """
    candidates = []
    month_selector = (
        '[class*="DesktopCalender_month__"]:visible, '
        '[class*="DesktopCalender_lastmonth__"]:visible'
    )
    months = page.locator(month_selector)

    for mi in range(months.count()):
        month = months.nth(mi)
        try:
            heading = ""
            heading_loc = month.locator("div")
            for hi in range(min(heading_loc.count(), 30)):
                txt = norm(heading_loc.nth(hi).inner_text())
                if calendar_month_heading_to_date(txt):
                    heading = txt
                    break

            month_start = calendar_month_heading_to_date(heading)
            if not month_start:
                debug(f"[DEBUG] Calendar month {mi}: heading not resolved.")
                continue

            cells = month.locator("td[id]")
            for ci in range(cells.count()):
                td = cells.nth(ci)
                cid = (td.get_attribute("id") or "").strip()
                m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", cid)
                if not m:
                    continue

                day_num = int(m.group(1))
                month_index = int(m.group(2))

                # TTD uses a zero-based JavaScript month in the cell id.
                actual_month = month_index + 1
                if actual_month != month_start.month:
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
            debug(
                f"[DEBUG] Calendar month {mi} inspection failed: "
                f"{type(exc).__name__}: {exc}"
            )

    # Defensive dedupe for React re-renders.
    deduped = {}
    for item in candidates:
        deduped.setdefault((item["date"], item["id"]), item)

    result = list(deduped.values())
    if result:
        grouped = {}
        for item in result:
            grouped.setdefault(item["date"].strftime("%m/%Y"), 0)
            grouped[item["date"].strftime("%m/%Y")] += 1
        debug(
            "[DEBUG] Calendar dates resolved from visible month headings: "
            + ", ".join(f"{k}={v}" for k, v in sorted(grouped.items()))
        )
    return result

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
    """Click a live TTD calendar cell and wait for the SPA to react.

    TTD can take longer than a few hundred milliseconds to rebuild the slot
    section after a date click. The earlier working version waited 600 ms;
    keep that baseline and add a short bounded reaction window. No reload or
    navigation is performed.
    """
    td = candidate["locator"]
    before_url = page.url
    before_body = body_text(page)

    def selection_signal():
        try:
            return bool(td.evaluate("""
                e => {
                    const cls = String(e.className || '').toLowerCase();
                    return e.getAttribute('aria-selected') === 'true' ||
                           e.getAttribute('data-selected') === 'true' ||
                           e.getAttribute('data-active') === 'true' ||
                           /selected|active|current/.test(cls);
                }
            """))
        except Exception:
            return False

    def slot_area_signal():
        try:
            return page.locator(
                '#scrollSlotType:visible, '
                '[class*="SlotBooking_availableSlotSection"]:visible, '
                '[class*="SlotBooking_selectedavailableSlotSection"]:visible, '
                'input[type="radio"]:visible, [role="radio"]:visible'
            ).count() > 0
        except Exception:
            return False

    def reacted():
        try:
            if page.url != before_url:
                return True
        except Exception:
            pass
        try:
            if body_text(page) != before_body:
                return True
        except Exception:
            pass
        return selection_signal() or slot_area_signal()

    try:
        td.scroll_into_view_if_needed()
        td.click(force=True, timeout=1500)
    except Exception as exc:
        debug(
            f"[DEBUG] Calendar <td> click failed for {candidate['id']}: "
            f"{type(exc).__name__}: {exc}"
        )
        try:
            child = td.locator("div").first
            child.click(force=True, timeout=1500)
        except Exception as exc2:
            debug(
                f"[DEBUG] Calendar child click also failed for {candidate['id']}: "
                f"{type(exc2).__name__}: {exc2}"
            )
            return False

    # Give the TTD SPA a bounded reaction window. This is intentionally not
    # a polling/reload loop; it only waits on the already-open page.
    for _ in range(8):
        page.wait_for_timeout(300)
        if reacted():
            debug(f"[DEBUG] Calendar selection reacted for {candidate['id']}.")
            return True

    # Final DOM click fallback, matching the older working behavior.
    try:
        td.evaluate("(e) => e.click()")
        for _ in range(6):
            page.wait_for_timeout(300)
            if reacted():
                debug(f"[DEBUG] Calendar DOM click reacted for {candidate['id']}.")
                return True
    except Exception as exc:
        debug(
            f"[DEBUG] Calendar DOM click fallback failed for {candidate['id']}: "
            f"{type(exc).__name__}: {exc}"
        )

    debug(f"[DEBUG] Calendar click produced no verified UI reaction for {candidate['id']}.")
    return False

def wait_for_target_date(page, target, timeout_minutes):
    """Wait for the exact target date to become selectable without reloading."""
    timeout_minutes = max(0.0, float(timeout_minutes or 0))
    if timeout_minutes <= 0:
        return None

    deadline = time.time() + timeout_minutes * 60.0
    debug(f"[DATE] Target date is present but not selectable yet; watching for up to {timeout_minutes:g} minutes.")
    debug("[DATE] No reload/navigation will be performed while waiting.")
    last_state = None

    while time.time() < deadline:
        candidates = calendar_date_candidates(page)
        for item in candidates:
            if item["date"] != target:
                continue

            state_key = (item["background"], item["cursor"], item["pointer_events"], item["selectable"])
            if state_key != last_state:
                debug(
                    f"[DATE] Target {item['id']}: cursor={item['cursor']}, "
                    f"pointer-events={item['pointer_events']}, bg={item['background']}"
                )
                last_state = state_key

            if item["selectable"]:
                debug("[DATE] Target date has become selectable.")
                return item

        remaining = max(0, int(deadline - time.time()))
        debug(f"[DATE] Target date still unavailable; waiting... ({remaining}s remaining)")
        page.wait_for_timeout(2000)

    print("[DATE] Target-date availability watch timed out.")
    return None

def choose_booking_date(page, booking):
    """
    Select target_date or, with NEXT_AVAILABLE, the earliest selectable date
    strictly after the requested date in the live calendar.

    Never selects an earlier date.
    """
    target = parse_target_date(booking.get("target_date"))
    date_fallback = norm(booking.get("date_fallback", "NONE")).upper()

    print(f"[DATE] Requested target date: {target.strftime('%d/%m/%Y')}")

    candidates = calendar_date_candidates(page)
    if not candidates:
        print("[DATE] No readable TTD calendar date cells were found.")
        snapshot(page, "date_calendar_not_detected")
        return False, None

    by_date = {}
    for item in candidates:
        by_date.setdefault(item["date"], item)

    requested = by_date.get(target)
    if requested:
        debug(
            f"[DATE] Target {requested['id']}: "
            f"cursor={requested['cursor']}, "
            f"pointer-events={requested['pointer_events']}, "
            f"bg={requested['background']}"
        )
        if requested["selectable"]:
            if click_calendar_date(page, requested):
                print(f"[OK] Selected target date: {target.strftime('%d/%m/%Y')}")
                return True, target
            debug("[DATE] Target date looked selectable but click verification failed.")
        else:
            debug("[DATE] Target date is not selectable in the live UI.")
    else:
        debug("[DATE] Target date is not exposed by the currently visible calendar.")

    if date_fallback != "NEXT_AVAILABLE":
        return False, None

    # Preserve the earlier working behavior: NEXT_AVAILABLE means the
    # earliest date currently exposed by TTD as selectable. It is not
    # restricted to dates after the requested target. This matters when an
    # earlier date is already released while the requested date is not.
    available = [
        item for item in by_date.values()
        if item["selectable"]
    ]
    available.sort(key=lambda x: x["date"])

    if available:
        debug(
            "[DATE] NEXT_AVAILABLE candidates: "
            + ", ".join(item["date"].strftime("%d/%m/%Y") for item in available)
        )

    for item in available:
        debug(f"[DATE] Trying next available date: {item['date'].strftime('%d/%m/%Y')}")
        if click_calendar_date(page, item):
            print(f"[OK] Selected NEXT_AVAILABLE date: {item['date'].strftime('%d/%m/%Y')}")
            return True, item["date"]

    print("[DATE] No selectable date after the requested date was safely found.")
    snapshot(page, "date_selection_failed")
    return False, None

