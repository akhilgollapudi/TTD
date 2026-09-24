# TTD Booking Assistant v19

## Important regression fix
v19 restores the missing `total_pilgrim_count` import in `runtime.py`. This was the cause of:

`NameError: name 'total_pilgrim_count' is not defined`

The function remains in `tickets.py` and is imported by `runtime.py` for both direct and package execution.

## Execution menu
After authentication/session preparation, the program shows:

1. Populate Screen 1 ONLY: Date + Number of Tickets + Slot
2. Populate Screen 2 ONLY: Pilgrim Details
CLOSE = Exit

Screen 1 and Screen 2 remain independently runnable. The browser/session remains open until CLOSE is explicitly selected.

## Run directly
```bash
cd /Users/akhilgollapudi/Desktop/project/TTD
source .venv/bin/activate
python main.py
```

## Package execution
From the parent directory:
```bash
python -m TTD
```

## Preserved behavior
- Existing authentication/persistent Chromium profile
- Manual OTP/CAPTCHA handling
- Independent Screen 1 / Screen 2 execution
- Date selection and forward-only NEXT_AVAILABLE fallback
- Actual TTD calendar variants including last visible month
- Number of Tickets
- Slot detection and selection across supported DOM variants
- Evening preference / availability ranking
- Pilgrim fields
- Email (`emailId` supported)
- City / State / Country / Pincode
- Gender / Photo ID dropdowns
- Continue handling
- Manual takeover / READY / CLOSE behavior
- Runtime diagnostics/screenshots

## Regression verification
The package was compile/import tested and verified to expose:
- `total_pilgrim_count`
- `wait_for_execution_mode`
- `run_execution_1`
- `run_execution_2`

The v15 baseline contained 55 top-level functions; v19 retains all 55 after modularization.

## v20 regression fix

This release preserves the v19 Screen 1 / Screen 2 independent execution menu and
fixes the TTD live calendar regression observed on 25-Sep-2026:
- `rgb(255, 191, 0)` (TTD "Filling Fast") is selectable.
- NEXT_AVAILABLE keeps the earlier working behavior of selecting the earliest
  currently selectable date exposed by TTD, rather than requiring it to be after
  the configured target date.
- No existing Screen 1/Screen 2 menu, persistent browser behavior, OTP/CAPTCHA
  manual handling, or form-filling modules are removed.

## v23 terminal output

Terminal output is concise by default: successful stage changes, configuration refreshes, manual-action requirements, and errors are shown. Detailed DOM/debug output is disabled by default.

For troubleshooting only, run:

```bash
TTD_VERBOSE=1 python main.py
```

`pilgrims.json` is checked immediately before each Screen 1/Screen 2 execution and again after menu input, so the latest pilgrim list and booking settings are used without restarting the browser.
