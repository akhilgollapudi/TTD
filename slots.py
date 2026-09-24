import re
import time

if __package__:
    from .config import *
    from .utils import norm, matches, body_text, snapshot
    from .calendar_handler import normalize_time_text, parse_slot_time
else:
    from config import *
    from utils import norm, matches, body_text, snapshot
    from calendar_handler import normalize_time_text, parse_slot_time

"""TTD Screen-1 slot discovery and ranking."""

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

                # A genuine slot card should also expose availability or quota.
                # Controls are optional because some current TTD variants make
                # the entire card/div the click target.
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

        # A plain clickable card is also a valid slot control.
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
        debug(
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
        debug(f"[DEBUG] Fast slot scan failed: {type(exc).__name__}: {exc}")

    return slot_card_candidates_fallback(page)

def click_slot(page, slot):
    """Click a TTD slot and verify that the selection state changed."""
    control = slot.get("button")
    card = slot["locator"]

    def selected_signal():
        try:
            if control is not None and control.count():
                ctype = (control.get_attribute("type") or "").lower()
                if ctype == "radio" and control.is_checked():
                    return True
                if (control.get_attribute("aria-checked") or "").lower() == "true":
                    return True

                sig = control.evaluate("""
                    e => ({
                        cls: String(e.className || '').toLowerCase(),
                        ariaPressed: e.getAttribute('aria-pressed'),
                        ariaChecked: e.getAttribute('aria-checked')
                    })
                """)
                if (
                    "selected" in sig["cls"]
                    or sig["ariaPressed"] == "true"
                    or sig["ariaChecked"] == "true"
                ):
                    return True

            sig = card.evaluate("""
                e => ({
                    cls: String(e.className || '').toLowerCase(),
                    selectedChild: !!e.querySelector(
                        '[aria-checked="true"], [aria-selected="true"], '
                        '[aria-pressed="true"], '
                        '[class*="selectedavailableSlotSection"]'
                    )
                })
            """)
            return sig["selectedChild"] or "selected" in sig["cls"]
        except Exception:
            return False

    try:
        if control is not None and control.count():
            control.scroll_into_view_if_needed()
            control.click(force=True, timeout=1500)
        else:
            card.scroll_into_view_if_needed()
            card.click(force=True, timeout=1500)

        page.wait_for_timeout(250)
        if selected_signal():
            print(f"[OK] Slot selected: {slot['time']}")
            return True

        page.wait_for_timeout(350)
        if selected_signal():
            print(f"[OK] Slot selected: {slot['time']}")
            return True

        # Some TTD builds expose no semantic selected state. The click itself
        # is still valid, so do not reject a successfully dispatched click.
        print(
            f"[SLOT] Click dispatched for {slot['time']}; "
            "no explicit selected state exposed by this DOM variant."
        )
        return True

    except Exception as exc:
        try:
            card.click(force=True, timeout=1500)
            page.wait_for_timeout(300)
            print(f"[OK] Slot card fallback click sent: {slot['time']}")
            return True
        except Exception:
            print(
                f"[SLOT] Slot click failed for {slot['time']}: "
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

def slot_priority(item, preferred="", evening_preferred=True, evening_start_minutes=18 * 60):
    """Rank slots with highest availability as the primary criterion."""
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
    """Choose the live slot with the highest numeric availability."""
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

