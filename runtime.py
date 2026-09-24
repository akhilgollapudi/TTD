from playwright.sync_api import sync_playwright

import json

if __package__:
    from .config import *
    from .utils import load_data, load_latest_data, data_file_signature, snapshot, booking_in_progress
    from .calendar_handler import choose_booking_date
    from .tickets import total_pilgrim_count, screen1_ticket_count, select_date_and_slot, click_ttd_continue
    from .forms import fill_pilgrims, fill_general
    from .auth import save_auth_if_authenticated, wait_for_auth_completion, ttd_login_screen
else:
    from config import *
    from utils import load_data, load_latest_data, data_file_signature, snapshot, booking_in_progress
    from calendar_handler import choose_booking_date
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

def run_execution_1(page, booking, ticket_count, data):
    """Screen 1 only: date + total ticket count + slot."""
    # Keep the JSON signature local to this execution. This is required for
    # the automatic Screen-1 -> Screen-2 transition, where pilgrims.json may
    # have changed while Screen 1 was running.
    data_signature = data_file_signature()

    print("\n[EXECUTION 1] Screen 1: Date + Tickets + Slot")
    print(f"[EXECUTION 1] Screen 1 ticket count: {ticket_count}")

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
                print("[EXECUTION 1] Screen 1 Continue was clicked.")

                # After a successful Screen 1 run, automatically continue into
                # Screen 2 and populate the configured pilgrim/general details.
                # Choice 2 in the main menu remains available for independent
                # Screen 2 execution at any time.
                if not wait_for_screen2(page, timeout_seconds=15):
                    snapshot(page, "screen2_not_detected_after_screen1")
                    hold_result = hold_for_manual_takeover(
                        page,
                        "Screen 1 Continue was clicked, but Screen 2 was not detected. "
                        "Manually bring up Screen 2, then retry or choose 2."
                    )
                    if hold_result in ("1", "2", "CLOSE"):
                        return hold_result
                    continue

                print("[AUTO] Screen 1 successful. Checking pilgrims.json for latest details before Screen 2...")
                data, data_signature, changed = load_latest_data(data, data_signature)
                # Keep the latest booking/pilgrim configuration for Screen 2.
                # If the user reduced 5 pilgrims to 2 while Screen 1 was
                # running, Screen 2 now receives exactly those latest 2 rows.
                if changed:
                    print(f"[CONFIG] Screen 2 using latest JSON: {total_pilgrim_count(data)} pilgrim(s).")
                result2 = run_execution_2(page, data)
                return result2

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
                return hold_for_manual_takeover(
                    page, "No pilgrim records were found in pilgrims.json."
                )

            unresolved = []
            print(f"[EXECUTION 2] Filling {len(pilgrims)} pilgrim(s).")
            fill_pilgrims(page, pilgrims, unresolved)
            fill_general(page, contact, unresolved, booking=booking)
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
                # IMPORTANT: always refresh configuration immediately before
                # accepting an execution choice. If 5 pilgrims were configured
                # and the user removes 3 because only 2 slots are available,
                # selecting 1 or 2 now uses exactly those latest 2 records.
                data, data_signature, changed = load_latest_data(data, data_signature)
                booking = data.get("booking", {})
                if changed:
                    print(f"[CONFIG] Latest pilgrim count: {total_pilgrim_count(data)}")

                mode = wait_for_execution_mode()
                if mode == "CLOSE":
                    print("[CLOSE] Explicit CLOSE received.")
                    return

                # Use the currently visible/latest tab after manual navigation.
                if context.pages:
                    page = context.pages[-1]

                # Check once more after the user answers 1/2. This closes the
                # small race where pilgrims.json is edited while the menu prompt
                # is waiting for input.
                data, data_signature, _ = load_latest_data(data, data_signature)
                booking = data.get("booking", {})
                ticket_count = screen1_ticket_count(page, data)

                if mode == "1":
                    result = run_execution_1(page, booking, ticket_count, data)
                else:
                    result = run_execution_2(page, data)

                # 1 or 2 can be selected from ANY hold/error point.
                while result in ("1", "2"):
                    # A retry/alternate execution can happen after the user
                    # edits pilgrims.json. Refresh again before re-entering.
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

