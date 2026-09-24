# v23 Gothram / Homam update

This build includes the working v23 code plus the Screen-2-specific behavior for:

**Sri Srinivasa Divyaanugraha Homam**

## Behavior

- For this seva only, Screen 1 ticket count comes from `booking.tickets` (normally `1`).
- Screen 2 can still contain multiple entries in `pilgrims.json`.
- `booking.gothram` is filled into the Gothram field for this seva only.
- Other sevas keep the existing behavior: Screen 1 ticket count is based on the number of pilgrims and Gothram is ignored.
- `pilgrims.json` is refreshed between Screen 1 and Screen 2, so the latest pilgrim list is used.
- Browser/session behavior, manual OTP/CAPTCHA, READY/retry, and independent Screen 1/Screen 2 menu choices are preserved.

Example:

```json
"booking": {
  "tickets": 1,
  "gothram": "srivatsa"
}
```

Use the existing `pilgrims.json` in your project; this package does not overwrite it.


## Date and slot selection update

- `date_fallback: NEXT_AVAILABLE` now means the earliest selectable date strictly after `target_date`; earlier dates are never selected.
- A successful date click is not followed by another date click in the same execution.
- Slot selection now prioritizes the highest numeric availability across all currently available slots. `preferred_slot` and `prefer_evening_slot` are tie-breakers only.
