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


def _calendar_scroll_state(page):
    """Return the horizontal scroll container used by the TTD month carousel."""
    try:
        return page.evaluate("""
        () => {
            const months = Array.from(document.querySelectorAll(
                '[class*=\"DesktopCalender_month__\"], [class*=\"DesktopCalender_lastmonth__\"]'
            )).filter(e => {
                const r = e.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            if (!months.length) return null;

            let el = months[0].parentElement;
            while (el && el !== document.body) {
                const style = getComputedStyle(el);
                const scrollable = el.scrollWidth > el.clientWidth + 8;
                if (scrollable && (style.overflowX === 'auto' || style.overflowX === 'scroll' || style.overflow === 'auto' || style.overflow === 'scroll')) {
                    return {scrollLeft: el.scrollLeft, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth};
                }
                el = el.parentElement;
            }
            return null;
        }
        """)
    except Exception:
        return None


def _scroll_calendar(page, direction):
    """Move the TTD calendar carousel one viewport left/right."""
    delta_sign = 1 if direction == "forward" else -1
    try:
        moved = page.evaluate("""
        (sign) => {
            const months = Array.from(document.querySelectorAll(
                '[class*=\"DesktopCalender_month__\"], [class*=\"DesktopCalender_lastmonth__\"]'
            )).filter(e => {
                const r = e.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            if (!months.length) return false;

            let el = months[0].parentElement;
            while (el && el !== document.body) {
                const style = getComputedStyle(el);
                if (el.scrollWidth > el.clientWidth + 8 &&
                    (style.overflowX === 'auto' || style.overflowX === 'scroll' || style.overflow === 'auto' || style.overflow === 'scroll')) {
                    const before = el.scrollLeft;
                    const amount = Math.max(300, Math.floor(el.clientWidth * 0.8));
                    el.scrollLeft = Math.max(0, Math.min(el.scrollWidth, before + sign * amount));
                    el.dispatchEvent(new Event('scroll', {bubbles:true}));
                    return el.scrollLeft !== before;
                }
                el = el.parentElement;
            }
            return false;
        }
        """, delta_sign)
        if moved:
            page.wait_for_timeout(350)
        return bool(moved)
    except Exception as exc:
        debug(f"[DEBUG] Calendar horizontal scroll failed: {type(exc).__name__}: {exc}")
        return False


def _calendar_visible_months(page):
    months = []
    for item in calendar_date_candidates(page):
        first = item["date"].replace(day=1)
        if first not in months:
            months.append(first)
    return sorted(months)


def _bring_target_month_into_view(page, target, max_moves=8):
    """Expose the target month without clicking any date."""
    target_month = target.replace(day=1)

    for _ in range(max_moves + 1):
        visible = _calendar_visible_months(page)
        if not visible:
            return False

        if target_month in visible:
            return True

        first_month = min(visible)
        last_month = max(visible)

        if target_month > last_month:
            if not _scroll_calendar(page, "forward"):
                break
        elif target_month < first_month:
            if not _scroll_calendar(page, "backward"):
                break
        else:
            return True

    return target_month in _calendar_visible_months(page)

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
    """Click exactly one TTD calendar date without bouncing between dates.

    The previous implementation treated the presence of the slot section as a
    successful date reaction. That section is often already present, so a
    click on one date could be reported as successful before TTD had actually
    applied the date. Combined with NEXT_AVAILABLE considering dates before
    the target, this could produce sequences such as 26 -> 27 -> 26.

    This version:
      * clicks only the supplied candidate;
      * never uses the pre-existing slot section as a success signal;
      * accepts an explicit selected/active DOM state when TTD exposes one;
      * otherwise treats the successful click dispatch as the result, because
        some TTD DOM variants do not expose a selected state.
    """
    td = candidate["locator"]

    def selection_state():
        try:
            return td.evaluate("""
                e => ({
                    aria: e.getAttribute('aria-selected'),
                    dataSelected: e.getAttribute('data-selected'),
                    dataActive: e.getAttribute('data-active'),
                    className: String(e.className || ''),
                    style: e.getAttribute('style') || '',
                    background: getComputedStyle(e).backgroundColor
                })
            """)
        except Exception:
            return None

    before = selection_state()

    try:
        td.scroll_into_view_if_needed()
        td.click(force=True, timeout=1500)
        page.wait_for_timeout(600)
    except Exception as exc:
        debug(
            f"[DEBUG] Calendar <td> click failed for {candidate['id']}: "
            f"{type(exc).__name__}: {exc}"
        )
        try:
            child = td.locator("div").first
            child.click(force=True, timeout=1500)
            page.wait_for_timeout(600)
        except Exception as exc2:
            debug(
                f"[DEBUG] Calendar child click also failed for {candidate['id']}: "
                f"{type(exc2).__name__}: {exc2}"
            )
            return False

    after = selection_state()

    # Prefer an explicit TTD selected/active state when it is available.
    if after:
        class_name = str(after.get("className") or "").lower()
        if (
            after.get("aria") == "true"
            or after.get("dataSelected") == "true"
            or after.get("dataActive") == "true"
            or re.search(r"selected|active|current", class_name)
        ):
            debug(f"[DATE] TTD marked {candidate['id']} as selected/active.")
            return True

    # Some current TTD variants do not expose selected state at all. If the
    # Playwright click succeeded, do not click another date trying to verify it.
    if before != after:
        debug(f"[DATE] Calendar state changed for {candidate['id']}.")
    print(
        f"[DATE] Click dispatched for {candidate['date'].strftime('%d/%m/%Y')}; "
        "TTD did not expose an explicit selected state."
    )
    return True

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
    """Select one date, searching calendar months before giving up.

    Priority:
      1. Exact target date.
      2. NEXT_AVAILABLE future date.
      3. If configured, search both directions for a selectable date.

    The function never clicks two dates as part of one successful selection.
    It first exposes the relevant month, then selects exactly one date.
    """
    target = parse_target_date(booking.get("target_date"))
    date_fallback = norm(booking.get("date_fallback", "NONE")).upper()
    search_direction = norm(booking.get("date_search_direction", "BOTH")).upper()
    max_month_moves = max(1, int(booking.get("date_search_months", 8) or 8))

    print(f"[DATE] Requested target date: {target.strftime('%d/%m/%Y')}")

    # First expose the requested month. This fixes the case where TTD initially
    # shows Aug-Nov while the configured target is in December.
    _bring_target_month_into_view(page, target, max_month_moves)

    candidates = calendar_date_candidates(page)
    if not candidates:
        print("[DATE] No readable TTD calendar date cells were found.")
        snapshot(page, "date_calendar_not_detected")
        return False, None

    by_date = {}
    for item in candidates:
        by_date.setdefault(item["date"], item)

    requested = by_date.get(target)
    if requested and requested["selectable"]:
        if click_calendar_date(page, requested):
            print(f"[OK] Selected target date: {target.strftime('%d/%m/%Y')}")
            return True, target

    if requested:
        debug(
            f"[DATE] Target {requested['id']} unavailable: "
            f"cursor={requested['cursor']}, pointer-events={requested['pointer_events']}, "
            f"bg={requested['background']}"
        )
    else:
        debug("[DATE] Target date is not currently exposed after month navigation.")

    if date_fallback != "NEXT_AVAILABLE":
        return False, None

    # Prefer a future date first. Do not select a date before target while a
    # future candidate is visible.
    future = sorted(
        (item for item in by_date.values() if item["date"] > target and item["selectable"]),
        key=lambda x: x["date"]
    )
    if future:
        item = future[0]
        if click_calendar_date(page, item):
            print(f"[OK] Selected NEXT_AVAILABLE date: {item['date'].strftime('%d/%m/%Y')}")
            return True, item["date"]

    # If the requested month has no future selectable date, optionally search
    # additional months forward. This is still date-only discovery; no date is
    # clicked until the final candidate is chosen.
    if search_direction in ("FORWARD", "BOTH"):
        for _ in range(max_month_moves):
            if not _scroll_calendar(page, "forward"):
                break
            candidates = calendar_date_candidates(page)
            future = sorted(
                (item for item in candidates if item["date"] > target and item["selectable"]),
                key=lambda x: x["date"]
            )
            if future:
                item = future[0]
                if click_calendar_date(page, item):
                    print(f"[OK] Selected NEXT_AVAILABLE date: {item['date'].strftime('%d/%m/%Y')}")
                    return True, item["date"]

    # BOTH is useful when TTD has no released future quota but an earlier date
    # is already bookable. This is opt-in through the default BOTH behavior.
    if search_direction == "BOTH":
        # Return the carousel to the target month before looking backward.
        _bring_target_month_into_view(page, target, max_month_moves)
        candidates = calendar_date_candidates(page)
        past = sorted(
            (item for item in candidates if item["date"] < target and item["selectable"]),
            key=lambda x: x["date"],
            reverse=True
        )
        if past:
            item = past[0]
            if click_calendar_date(page, item):
                print(f"[OK] Selected nearby available date: {item['date'].strftime('%d/%m/%Y')}")
                return True, item["date"]

    print(
        "[DATE] Target date is unavailable and no selectable fallback date "
        "was exposed after searching the calendar months."
    )
    snapshot(page, "date_selection_failed")
    return False, None

