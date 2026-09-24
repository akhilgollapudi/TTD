# TTD Booking Assistant v24

## Full update
This package preserves the modular v23 booking assistant and includes the latest
calendar/date-search update plus the Screen-1/Screen-2 JSON refresh fixes.

## Execution menu
After authentication/session preparation, the program shows:

1. Populate Screen 1 ONLY: Date + Number of Tickets + Slot
2. Populate Screen 2 ONLY: Pilgrim Details
CLOSE = Exit

Screen 1 and Screen 2 remain independently runnable. The browser/session remains
open until CLOSE is explicitly selected.

## Date selection update
- The configured `target_date` is attempted first.
- The calendar month is exposed by scrolling the TTD calendar carousel when the
  target month is not initially visible.
- `date_fallback: NEXT_AVAILABLE` selects a selectable date strictly after the
  target date.
- `date_search_direction: FORWARD` or `BOTH` can extend the search across
  additional calendar months. Default is `BOTH`.
- `date_search_months` controls the maximum month-navigation attempts. Default is 8.
- A successful execution selects exactly one date; it does not bounce between dates.

## Slot selection update
- The live slot inventory is scanned once for the selected date.
- Highest numeric availability is the primary selection rule.
- `preferred_slot` and `prefer_evening_slot` are tie-breakers only.
- No extra date changes or repeated slot searches are performed after selection.

## Gothram / Homam
For **Sri Srinivasa Divyaanugraha Homam**:
- Screen 1 ticket count comes from `booking.tickets` (normally 1).
- Screen 2 may still contain multiple pilgrim entries.
- `booking.gothram` is filled for this seva only.
- Other sevas keep the existing behavior: Screen 1 ticket count is based on the
  number of pilgrims and Gothram is ignored.

## JSON refresh
`pilgrims.json` can be edited while the browser remains open. The latest valid
configuration is loaded before Screen 1/Screen 2 execution and before the automatic
Screen-1 -> Screen-2 transition.

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
- Persistent Chromium profile/session
- Manual OTP/CAPTCHA handling
- Independent Screen 1 / Screen 2 execution
- Number of Tickets
- TTD calendar variants including last visible month
- Slot detection and availability parsing
- Pilgrim fields and contact/general details
- Email (`emailId` supported)
- City / State / Country / Pincode
- Gender / Photo ID dropdowns
- Continue handling
- Manual takeover / READY / CLOSE behavior
- Runtime diagnostics/screenshots

## Verification
All Python modules in this package were compile-tested with Python 3.
The package was not live-tested against the TTD website in this build step.

For troubleshooting only:
```bash
TTD_VERBOSE=1 python main.py
```
