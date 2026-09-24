# Updated dual-trigger flow

The runtime now supports both Screen 1 triggers:

1. **Backend trigger:** type `1` in the terminal. This keeps the existing automatic date-selection behavior using `booking.target_date` and its configured fallback rules.
2. **Browser trigger:** leave the runtime waiting and click a TTD calendar date in the browser. The runtime detects the calendar date click and uses that exact date for the Screen 1 slot-selection flow.

Both triggers converge on the same Screen 1 -> slot -> Continue -> Screen 2 -> pilgrim details flow.

`2` still runs Screen 2 independently, and `CLOSE` exits.

The browser date listener only records date-cell clicks; it does not click dates itself. A duplicate trigger is not started while an execution is already running.
