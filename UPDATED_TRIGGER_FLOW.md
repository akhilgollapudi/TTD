# Updated Trigger / Persistent Session Flow

## Triggers available whenever the script is in READY mode

- `1` -> run Screen 1 using `booking.target_date`.
- `2` -> run Screen 2 using the current browser page.
- Browser date click -> run Screen 1 using the exact date clicked.
- `CLOSE` -> explicitly end the script/browser session.

The browser date listener is reinstalled every time the script returns to READY.

## Failure rule

Automation failures no longer enter a blocking HOLD prompt during normal execution.
The browser/context/session is kept open and the script returns to READY.
This means the user can immediately:

- click another date in the browser, or
- choose `1`, or
- choose `2`.

No logout, browser close, context close, re-login, or queue restart is performed by a normal automation failure.

## Important usage rule

Do not enter `1` or `2` at the same moment as a browser date click. A browser date click is already a complete Screen-1 trigger. If a backend command is used, wait until the script displays READY and then enter the command.

## Screen 2 behavior

If `2` is selected while the current page is not a Screen-2 pilgrim form, field verification will fail safely and the script returns to READY without closing the session. After manually navigating to the correct Screen-2 page, select `2` again.

For Divyaanugraha, the Screen-2 form is treated as a fixed two-person form even if `pilgrims.json` contains more pilgrims for another seva.
