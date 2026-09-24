import re
import time

if __package__:
    from .config import *
    from .utils import norm, matches, body_text, snapshot, is_gothram_seva
    from .slots import slot_card_candidates, click_slot
    from .calendar_handler import choose_booking_date, normalize_time_text, parse_slot_time
else:
    from config import *
    from utils import norm, matches, body_text, snapshot, is_gothram_seva
    from slots import slot_card_candidates, click_slot
    from calendar_handler import choose_booking_date, normalize_time_text, parse_slot_time

"""Ticket count and Continue controls."""
import re
import time
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

def screen1_ticket_count(page, data):
    """Return the ticket count required by the currently selected seva.

    Normal SED behavior remains one ticket per configured pilgrim. The
    Srinivasa Divyaanugraha Homam form is different: the Screen-1 booking
    ticket count is fixed from booking.tickets (normally 1), while Screen 2
    may contain multiple pilgrim rows.
    """
    if is_gothram_seva(page):
        booking = data.get("booking", {}) if isinstance(data, dict) else {}
        configured = booking.get("tickets", 1)
        try:
            count = int(configured)
        except (TypeError, ValueError):
            count = 1
        return max(1, count)
    return total_pilgrim_count(data)

def set_ttd_ticket_count(page, ticket_count, required=False):
    """Set Number of Tickets when this Screen-1 variant exposes the field.

    The supplied actual TTD Screen-1 capture has no Number of Tickets control;
    it shows Darshan Slots followed by Additional Services.
    """
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
        print(
            "[TICKETS] Number of Tickets control is not exposed on this "
            "Screen 1 variant; continuing without it."
        )
        if required:
            snapshot(page, "ticket_control_not_found")
        return not required

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

def slot_priority(item, preferred="", evening_preferred=True, evening_start_minutes=16 * 60):
    """Rank slots with HIGHEST AVAILABILITY as the primary rule.

    Priority:
      1. Highest numeric availability.
      2. preferred_slot as a tie-breaker.
      3. evening preference as a tie-breaker.
      4. Earliest slot time as the final tie-breaker.

    ``prefer_evening_slot`` no longer overrides a slot with higher
    availability. This means a 4 PM slot with 77 available beats a 6 PM slot
    with 60 available. Evening preference only helps when availability is tied.
    """
    count = item.get("availability_count")
    known = count is not None
    minutes = item.get("minutes")
    is_evening = (
        evening_preferred
        and minutes is not None
        and minutes >= evening_start_minutes
    )
    preferred_match = bool(preferred and item.get("time") == preferred)

    return (
        1 if known else 0,
        count if known else -1,
        1 if preferred_match else 0,
        1 if is_evening else 0,
        -(minutes if minutes is not None else 9999),
    )


def choose_booking_slot(page, booking, ticket_count=None):
    """Select the currently available slot with the highest availability.

    The entire visible inventory is scanned once. No date changes, reloads,
    or repeated slot searches are performed after a candidate is selected.
    """
    preferred = normalize_time_text(booking.get("preferred_slot", ""))

    evening_start_text = normalize_time_text(
        booking.get("evening_start_time", "6:00 PM")
    )
    evening_start_minutes = parse_slot_time(evening_start_text)
    if evening_start_minutes is None:
        evening_start_minutes = 18 * 60

    evening_preferred = bool(booking.get("prefer_evening_slot", True))

    slots = slot_card_candidates(page)

    if not slots:
        print("[SLOT] No TTD slot cards were detected; failing fast.")
        snapshot(page, "slot_cards_not_detected")
        return False, None

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
            debug(
                f"[SLOT] Skipping {item['time']} - capacity "
                f"{item['capacity']} < tickets {requested_tickets}"
            )
            continue

        available.append(item)

    debug("[SLOT] Live slot inventory:")
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
        key=lambda item: slot_priority(
            item,
            preferred,
            evening_preferred=evening_preferred,
            evening_start_minutes=evening_start_minutes,
        ),
        reverse=True,
    )

    selected = ranked[0]
    print(
        f"[SLOT] Highest availability: {selected.get('availability_count')} "
        f"at {selected['time']}"
    )

    if click_slot(page, selected):
        print(f"[OK] Selected SED slot: {selected['time']}")
        return True, selected

    print("[SLOT] Selected slot click failed; failing fast.")
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

    # Some TTD Screen-1 variants expose Number of Tickets; the supplied
    # actual variant does not. Set it when present, but do not fail merely
    # because this newer form omits the field.
    debug(f"[TICKETS] Total pilgrims in config: {ticket_count}")
    if not set_ttd_ticket_count(page, ticket_count, required=False):
        print("[TICKETS] Number of Tickets could not be set on this variant.")
        return False

    print(
        f"[SLOT] Checking available slots for "
        f"{selected_date.strftime('%d/%m/%Y')}..."
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

