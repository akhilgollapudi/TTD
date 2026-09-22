# TTD Booking Assistant v4

This version incorporates the exact field structure visible in the supplied TTD screenshot.

## Screenshot fields

Darshan:
- Darshan Date
- Darshan Slot
- No. of Tickets
- No. of Laddus
- Hundi Offerings

Pilgrim:
- Name
- Age
- Gender
- Photo ID Proof
- Photo ID Number

General:
- Email
- City
- State
- Country
- Pincode

## Date/slot fallback

Edit `pilgrims.json`:

```json
"target_date": "DD/MM/YYYY",
"date_fallback": "NEXT_AVAILABLE",
"preferred_slot": "09:00 PM",
"slot_fallback": "EARLIEST_AVAILABLE"
```

Behavior:
1. Try the requested date.
2. If that date is not exposed as an enabled option, look for a later enabled date.
3. Never choose an earlier date than requested.
4. On the selected date, try the preferred time slot.
5. If that slot is unavailable, choose the earliest available slot when
   `slot_fallback` is `EARLIEST_AVAILABLE`.
6. If the live UI cannot be interpreted safely, stop and ask you to select it manually.

Because TTD's live calendar/slot implementation can change, the fallback is intentionally
conservative. It only uses dates/times it can identify as enabled from the rendered page.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Run:

```bash
python ttd_booking.py
```

Keep `pilgrims.json` private; it can contain Aadhaar numbers.

OTP/CAPTCHA, anti-bot/queue controls and payment are manual.
