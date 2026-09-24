if __package__:
    from .config import *
else:
    from config import *

from urllib.parse import parse_qs, unquote, urlparse

"""Fast shared DOM/state helpers."""

def selected_seva_name(page):
    """Return the currently selected TTD seva name without navigation."""
    try:
        query = parse_qs(urlparse(page.url).query)
        values = query.get("sevaName") or query.get("seva_name")
        if values and values[0].strip():
            return unquote(values[0]).strip()
    except Exception:
        pass

    # Some TTD screen variants keep the seva name only in visible page text.
    try:
        body = body_text(page)
        target = norm(GOTHRAM_SEVA_NAME)
        for line in body.splitlines():
            if norm(line) == target:
                return line.strip()
    except Exception:
        pass

    return ""

def is_gothram_seva(page):
    """True when the current TTD page is the Divyaanugraha Homam form.

    TTD has used slightly different spelling/casing in URL text and visible
    cards (Divyaanugraha/Divyanugraha). Prefer the explicit seva name when
    available, but also recognize the distinctive visible Homam label so a
    spelling variation does not accidentally route the form through generic
    Screen-1 rules.
    """
    selected = norm(selected_seva_name(page))
    target = norm(GOTHRAM_SEVA_NAME)
    if selected == target:
        return True
    if "divyaanugraha" in selected or "divyanugraha" in selected:
        return "homam" in selected

    try:
        body = norm(body_text(page))
        return (
            ("sri srinivasa divyaanugraha homam" in body)
            or ("sri srinivasa divyanugraha homam" in body)
        )
    except Exception:
        return False

def norm(v):
    return re.sub(r"\s+", " ", (v or "").strip().lower())

def matches(text, patterns):
    text = norm(text)
    return any(re.search(p, text, re.I) for p in patterns)

def load_data():
    """Load the current pilgrims.json from disk."""
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def data_file_signature():
    """Return a cheap signature for pilgrims.json without reading its contents."""
    try:
        stat = DATA_FILE.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def load_latest_data(previous_data=None, previous_signature=None):
    """
    Reload pilgrims.json only when it changed.

    This is intentionally checked at execution boundaries so editing the JSON
    never requires restarting the browser/session and does not add polling
    overhead while the automation is running. If the file is temporarily
    invalid while the user is saving it, keep the last known-good data and let
    the next execution boundary try again.

    Returns: (data, signature, changed)
    """
    signature = data_file_signature()

    if previous_data is not None and signature == previous_signature:
        return previous_data, previous_signature, False

    try:
        data = load_data()
    except (OSError, json.JSONDecodeError) as exc:
        if previous_data is not None:
            print(f"[CONFIG] pilgrims.json is being updated; keeping last valid configuration: {type(exc).__name__}")
            return previous_data, previous_signature, False
        raise

    if previous_data is None:
        print(f"[CONFIG] Loaded pilgrims.json: {len(data.get('pilgrims', []))} pilgrim(s).")
    else:
        print("[CONFIG] pilgrims.json changed. Loaded the latest configuration.")
        print(f"[CONFIG] Latest pilgrim count: {len(data.get('pilgrims', []))}")

    return data, signature, True

def snapshot(page, label):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png = ARTIFACT_DIR / f"{stamp}_{label}.png"
    html = ARTIFACT_DIR / f"{stamp}_{label}.html"
    try: page.screenshot(path=str(png), full_page=True)
    except Exception: pass
    try: html.write_text(page.content(), encoding="utf-8")
    except Exception: pass
    debug(f"[DEBUG] Saved: {png}")

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

