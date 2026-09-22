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

## pilgrims.json format

The `pilgrims.json` file contains booking settings, contact details, and a list of pilgrims to book for. Keep this file private as it may include ID numbers.

Example (full file):

```json
{
   "booking": {
      "start_time": "09:20",
      "timezone": "Asia/Kolkata",
      "watch_timeout_minutes": 45,
      "target_date": "01/12/2026",
      "date_fallback": "NEXT_AVAILABLE",
      "preferred_slot": "09:00 PM",
      "slot_fallback": "EARLIEST_AVAILABLE",
      "tickets": 1,
      "laddoo_count": 2,
      "hundi_offering": "0"
   },
   "contact": {
      "email": "you@example.com",
      "city": "Hyderabad",
      "state": "Telangana",
      "country": "India",
      "pincode": "500085"
   },
   "pilgrims": [
      {
         "name": "Full Name",
         "age": 30,
         "gender": "Male",
         "id_type": "Aadhaar",
         "id_number": ""
      }
   ]
}
```

Field descriptions:

- `booking.start_time`: local time to start the booking attempt (HH:MM).
- `booking.timezone`: timezone string, e.g. `Asia/Kolkata`.
- `booking.watch_timeout_minutes`: how long (minutes) to keep trying.
- `booking.target_date`: preferred date in `DD/MM/YYYY`.
- `booking.date_fallback`: `NEXT_AVAILABLE` or `STRICT`.
- `booking.preferred_slot`: preferred slot label (e.g. `09:00 PM`).
- `booking.slot_fallback`: `EARLIEST_AVAILABLE` or `STRICT`.
- `booking.tickets`: number of tickets to book.
- `booking.laddoo_count`: number of laddoos.
- `booking.hundi_offering`: hundi offering amount as string.
- `contact.*`: contact details used on the booking form.
- `pilgrims[]`: array of pilgrim objects; each must include `name`, `age`, `gender`, `id_type`, and `id_number`.

Notes:
- Keep Aadhaar or other ID numbers secure and never commit them publicly.
- If you change the `pilgrims.json` structure, update `ttd_booking.py` accordingly.
