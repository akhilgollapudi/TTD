from playwright.sync_api import sync_playwright

import json
import os
import select
import sys

if __package__:
    from .config import *
    from .utils import load_data, load_latest_data, data_file_signature, snapshot, booking_in_progress
    from .calendar_handler import install_browser_date_listener, clear_browser_date_click, get_browser_date_click
    from .tickets import total_pilgrim_count, screen1_ticket_count, select_date_and_slot, click_ttd_continue
    from .forms import fill_pilgrims, fill_general
    from .auth import save_auth_if_authenticated, wait_for_auth_completion, ttd_login_screen
else:
    from config import *
    from utils import load_data, load_latest_data, data_file_signature, snapshot, booking_in_progress
    from calendar_handler import install_browser_date_listener, clear_browser_date_click, get_browser_date_click
    from tickets import total_pilgrim_count, screen1_ticket_count, select_date_and_slot, click_ttd_continue
    from forms import fill_pilgrims, fill_general
    from auth import save_auth_if_authenticated, wait_for_auth_completion, ttd_login_screen

"""Interactive execution/hold workflow."""

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

def _stdin_ready(timeout=0.0):
    """Return True when a console command is ready without blocking Playwright."""
    if os.name == "nt":
        try:
            import msvcrt
            return msvcrt.kbhit()
        except Exception:
            return False

    try:
        readable, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(readable)
    except (OSError, ValueError):
        return False


def _read_command_if_ready():
    """Read one complete terminal command, if one is available."""
    if not _stdin_ready(0):
        return None

    try:
        command = sys.stdin.readline().strip().upper()
    except Exception:
        return None

    return command or "READY"


def wait_for_trigger(page):
    """Wait for either backend command 1/2 or a date click in the browser.

    This is deliberately non-blocking: Playwright keeps polling the page while
    stdin is also checked, so either trigger can win.
    """
    install_browser_date_listener(page)
    clear_browser_date_click(page)

    print("\n" + "=" * 72)
    print("[READY] Waiting for an execution trigger...")
    print("  1 = Backend: run Screen 1 using booking.target_date")
    print("  2 = Backend: run Screen 2 only")
    print("  BROWSER = Click any TTD calendar date to start Screen 1 for that date")
    print("  CLOSE = Exit")
    print("[READY] You can leave this running and click a date when quota opens.")

    while True:
        command = _read_command_if_ready()
        if command in ("1", "2", "CLOSE"):
            clear_browser_date_click(page)
            return command, None

        # A date click is captured in the browser and resolved against the
        # current visible TTD calendar by Python.
        selected_date = get_browser_date_click(page)
        if selected_date is not None:
            # Give the TTD click handler a short moment to finish updating the
            # React UI before the slot inventory is inspected.
            page.wait_for_timeout(400)
            return "BROWSER", selected_date

        page.wait_for_timeout(150)


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

def wait_for_screen2(page, timeout_seconds=15):
    """Wait briefly for TTD Screen 2 to render after Screen 1 Continue."""
    deadline = time.time() + max(1, int(timeout_seconds))
    selectors = [
        '[name="fname"]:visible',
        '[name="name"]:visible',
        'input[name="age"]:visible',
        'input[name="idProofNumber"]:visible',
    ]
    while time.time() < deadline:
        for selector in selectors:
            try:
                if page.locator(selector).count():
                    print(f"[OK] Screen 2 detected via {selector}.")
                    return True
            except Exception:
                pass
        try:
            text = page.locator("body").inner_text(timeout=1000).lower()
            if "pilgrim details" in text and ("general details" in text or "email" in text):
                print("[OK] Screen 2 detected from page content.")
                return True
        except Exception:
            pass
        page.wait_for_timeout(300)
    return False

def run_execution_1(page, booking, ticket_count, data, selected_date=None):
    """Screen 1 only: date + total ticket count + slot."""
    # Keep the JSON signature local to this execution. This is required for
    # the automatic Screen-1 -> Screen-2 transition, where pilgrims.json may
    # have changed while Screen 1 was running.
    data_signature = data_file_signature()

    print("\n[EXECUTION 1] Screen 1: Date + Tickets + Slot")
    print(f"[EXECUTION 1] Screen 1 ticket count: {ticket_count}")
    if selected_date is not None:
        print(
            f"[EXECUTION 1] Trigger source: browser date click "
            f"({selected_date.strftime('%d/%m/%Y')})"
        )

    while True:
        try:
            if booking_in_progress(page):
                snapshot(page, "execution1_booking_in_progress")
                print(
                    "[READY] TTD reports that a booking is already in progress. "
                    "No browser/session close will be performed. Returning to the "
                    "main trigger listener; you can click another date or choose 1/2."
                )
                return "READY"

            ok = select_date_and_slot(page, booking, ticket_count, selected_date=selected_date)
            if ok:
                save_auth_if_authenticated(page.context, page)
                print("[EXECUTION 1] Date, ticket count, and slot completed.")
                print("[EXECUTION 1] Screen 1 Continue was clicked.")

                # After a successful Screen 1 run, automatically continue into
                # Screen 2 and populate the configured pilgrim/general details.
                # Choice 2 in the main menu remains available for independent
                # Screen 2 execution at any time.
                if not wait_for_screen2(page, timeout_seconds=15):
                    snapshot(page, "screen2_not_detected_after_screen1")
                    print(
                        "[READY] Screen 2 was not detected after Screen 1. "
                        "Browser/session remains OPEN. Returning to the main trigger "
                        "listener so you can click a date or choose 1/2."
                    )
                    return "READY"

                print("[AUTO] Screen 1 successful. Checking pilgrims.json for latest details before Screen 2...")
                data, data_signature, changed = load_latest_data(data, data_signature)
                # Keep the latest booking/pilgrim configuration for Screen 2.
                # If the user reduced 5 pilgrims to 2 while Screen 1 was
                # running, Screen 2 now receives exactly those latest 2 rows.
                if changed:
                    print(f"[CONFIG] Screen 2 using latest JSON: {total_pilgrim_count(data)} pilgrim(s).")
                result2 = run_execution_2(page, data, browser_triggered=(selected_date is not None))
                return result2

            snapshot(page, "execution1_date_slot_failed")

            # When Screen 1 was started by clicking a date in the browser, a
            # selected date can legitimately have no compatible/live slot.
            # Do NOT enter the blocking manual HOLD in that case: the user may
            # simply click another calendar date, and the browser trigger must
            # remain live. Returning to the main trigger loop reinstalls the
            # listener and allows the next date click to start a fresh attempt.
            if selected_date is not None:
                print(
                    "[BROWSER] No usable slot for the selected date. "
                    "Returning to browser-date listening mode; click another "
                    "date to retry."
                )
                return "BROWSER_RETRY"

            print(
                "[READY] Screen 1 could not be completed. Browser/session remains OPEN. "
                "Returning to the main trigger listener; click a date or choose 1/2."
            )
            return "READY"

        except Exception as exc:
            print(f"\n[EXECUTION 1 ERROR] {type(exc).__name__}: {exc}")
            snapshot(page, "execution1_exception")
            save_auth_if_authenticated(page.context, page)
            if selected_date is not None:
                print("[BROWSER] Screen 1 exception; returning to browser-date listening without closing the session.")
                return "BROWSER_RETRY"
            print(
                "[READY] Execution 1 failed, but the browser/session remains OPEN. "
                "Returning to the main trigger listener; click a date or choose 1/2."
            )
            return "READY"

def run_execution_2(page, data, browser_triggered=False):
    """Screen 2: populate the latest pilgrim/contact configuration, then Continue."""
    print("\n[EXECUTION 2] Screen 2: Pilgrim Details")

    # Keep a local signature so a user can edit pilgrims.json during a manual
    # hold/retry and the next retry automatically picks up the new rows.
    data_signature = data_file_signature()

    while True:
        try:
            data, data_signature, changed = load_latest_data(data, data_signature)
            if changed:
                print("[CONFIG] Screen 2 refreshed from the latest pilgrims.json.")

            pilgrims = data.get("pilgrims", [])
            contact = data.get("contact", {})
            booking = data.get("booking", {})
            if not isinstance(pilgrims, list) or not pilgrims:
                print(
                    "[READY] No pilgrim records were found in pilgrims.json. "
                    "Browser/session remains OPEN; returning to the main trigger listener."
                )
                return "READY"

            unresolved = []
            print(f"[EXECUTION 2] Filling {len(pilgrims)} pilgrim(s).")
            fill_pilgrims(page, pilgrims, unresolved)
            fill_general(page, contact, unresolved, booking=booking)
            snapshot(page, "execution2_pilgrim_details")

            if unresolved:
                print(f"[EXECUTION 2] {len(unresolved)} field(s) need manual attention.")
                for item in unresolved:
                    print(f"[MANUAL] {item[0]} -> {item[2]}")
                print(
                    "[READY] Screen 2 field verification failed. Browser/session remains OPEN. "
                    "Returning to the main trigger listener; you can click a date or choose 1/2."
                )
                return "READY"

            print("[EXECUTION 2] Pilgrim/contact details populated successfully.")

            # Screen 2 is complete after Pilgrim Details are populated.
            # Generic/General Details are filled only when that form is present.
            # Then click Continue once.
            if not click_ttd_continue(page, 1, "Screen 2"):
                print("[CONTINUE] Screen 2 Continue failed.")
                snapshot(page, "screen2_continue_failed")
                print(
                    "[READY] Screen 2 Continue failed. Browser/session remains OPEN. "
                    "Returning to the main trigger listener; you can click a date or choose 1/2."
                )
                return "READY"

            save_auth_if_authenticated(page.context, page)
            print("[OK] Screen 2 Continue clicked.")
            print("[EXECUTION 2] Browser remains open for final manual review.")
            snapshot(page, "screen2_continue_complete")
            return True

        except Exception as exc:
            print(f"\n[EXECUTION 2 ERROR] {type(exc).__name__}: {exc}")
            snapshot(page, "execution2_exception")
            save_auth_if_authenticated(page.context, page)
            print(
                "[READY] Execution 2 failed, but the browser/session remains OPEN. "
                "Returning to the main trigger listener; you can click a date or choose 1/2."
            )
            return "READY"

def main():
    data = load_data()
    data_signature = None
    booking = data.get("booking", {})

    with sync_playwright() as p:
        context = None
        page = None
        try:
            BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            debug(f"[DEBUG] Browser profile: {BROWSER_PROFILE_DIR}")
            debug(f"[DEBUG] Profile exists: {BROWSER_PROFILE_DIR.exists()}")

            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_DIR),
                headless=False,
                viewport={"width": 1440, "height": 950},
                timezone_id=booking.get("timezone", "Asia/Kolkata")
            )

            debug(f"[DEBUG] Pages opened: {len(context.pages)}")
            page = context.pages[0] if context.pages else context.new_page()

            try:
                current_url = page.url
            except Exception:
                current_url = ""

            if not current_url.startswith("https://ttdevasthanams.ap.gov.in"):
                page.goto(TTD_URL, wait_until="domcontentloaded", timeout=60000)


            if ttd_login_screen(page):
                if not wait_for_auth_completion(page, context):
                    print("[AUTH] Authentication is incomplete; browser will remain open.")
                    return
            print("\nBOOKING PREPARATION:")
            print("1. If TTD asks for OTP/CAPTCHA, complete it manually.")
            print("2. You can run Screen 1 and Screen 2 independently at any time.")
            print("3. The browser/session stays open unless you explicitly choose CLOSE.")

            save_auth_if_authenticated(context, page)

            # Establish the current configuration signature. The JSON is
            # re-checked at every execution boundary, so the user can edit
            # pilgrims.json while this browser/session remains open.
            data, data_signature, _ = load_latest_data(data, data_signature)

            while True:
                # Keep the browser event listener active while waiting. This
                # allows either a terminal command or a real browser date click
                # to trigger the same Screen-1 execution path.
                if context.pages:
                    page = context.pages[-1]

                data, data_signature, changed = load_latest_data(data, data_signature)
                booking = data.get("booking", {})
                if changed:
                    print(f"[CONFIG] Latest pilgrim count: {total_pilgrim_count(data)}")

                mode, selected_date = wait_for_trigger(page)
                if mode == "CLOSE":
                    print("[CLOSE] Explicit CLOSE received.")
                    return

                # Refresh once more immediately before execution so edits to
                # pilgrims.json made while waiting are honored.
                data, data_signature, _ = load_latest_data(data, data_signature)
                booking = data.get("booking", {})
                ticket_count = screen1_ticket_count(page, data)

                if mode == "1":
                    result = run_execution_1(page, booking, ticket_count, data)
                elif mode == "BROWSER":
                    result = run_execution_1(
                        page, booking, ticket_count, data, selected_date=selected_date
                    )
                else:
                    result = run_execution_2(page, data)

                # 1 or 2 can still be selected from ANY hold/error point.
                # Browser-date triggers are accepted only while the main idle
                # trigger loop is active, so there is no competing second flow.
                while result in ("1", "2"):
                    data, data_signature, _ = load_latest_data(data, data_signature)
                    booking = data.get("booking", {})
                    if context.pages:
                        page = context.pages[-1]
                    ticket_count = screen1_ticket_count(page, data)
                    if result == "1":
                        result = run_execution_1(page, booking, ticket_count, data)
                    else:
                        result = run_execution_2(page, data)

                if result == "CLOSE":
                    print("[CLOSE] Explicit CLOSE received.")
                    return

                if result in ("BROWSER_RETRY", "READY"):
                    print(
                        "[READY] Browser-date trigger remains active. Browser/session remains OPEN. "
                        "You may click any TTD date, or choose 1/2 from the backend."
                    )
                    continue

                print("\n[READY] Execution completed/paused. Browser remains OPEN.")

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

                print(
                    "[READY] An unexpected error occurred, but the browser/session is being kept OPEN. "
                    "The trigger listener will remain available so you can click a date or choose 1/2."
                )
                # Do not terminate the authenticated browser on an automation error.
                # Re-enter the same trigger loop and let the user decide the next action.
                while True:
                    try:
                        if context is None or not context.pages:
                            print("[ERROR] Browser context is no longer available; cannot preserve the session.")
                            return
                        page = context.pages[-1]
                        mode, selected_date = wait_for_trigger(page)
                        if mode == "CLOSE":
                            print("[CLOSE] Explicit CLOSE received.")
                            return
                        data, data_signature, _ = load_latest_data(data, data_signature)
                        booking = data.get("booking", {})
                        ticket_count = screen1_ticket_count(page, data)
                        if mode == "1":
                            result = run_execution_1(page, booking, ticket_count, data)
                        elif mode == "2":
                            result = run_execution_2(page, data)
                        else:
                            result = run_execution_1(page, booking, ticket_count, data, selected_date=selected_date)
                        if result == "CLOSE":
                            return
                        print("[READY] Browser/session remains OPEN. Returning to trigger listener.")
                    except Exception as loop_exc:
                        print(f"[ERROR] Recovery loop: {type(loop_exc).__name__}: {loop_exc}")
                        if page is not None:
                            try:
                                save_auth_if_authenticated(context, page)
                            except Exception:
                                pass
                        print("[READY] Recovery will continue without closing the browser/session.")
            else:
                input("[HOLD] Browser was not fully initialized. Press ENTER to exit...")

