# Night-out planner

## Procedure
1. **Collect** city, date + time (convert to ISO 8601 local, e.g. `2026-10-22T19:00:00`),
   party size, cuisine and budget (price tier 1–4). Ask only for what is missing; city and
   party size are required.
2. **Weather** — run `python3 scripts/get_weather.py --city "<city>"`.
   - The result is *current* conditions as JSON. It is **not a forecast**: if the booking is
     not for today, say so and do not predict the weather for that day.
   - Read `weathercode` (WMO): 0 clear · 1–3 cloudy · 45/48 fog · 51–57 drizzle · 61–67 rain ·
     71–77 snow · 80–82 showers · 95–99 thunderstorm. `temperature` is °F.
   - For tonight with code ≥ 51 or temperature < 45 °F: prefer restaurants with
     `distance_km` ≤ 2 and mention an umbrella or coat. If the tool is unavailable, say so in
     one line and continue — weather never blocks a booking.
3. **Search** — `python3 scripts/search_restaurants.py --city … [--cuisine …] [--price_tier …] [--party_size …]`.
   Present at most 3 options: name, cuisine, price tier as `$`, rating, address.
4. **Availability** — `python3 scripts/check_availability.py --restaurant_id … --date_time … --party_size …`
   for the user's pick. If the requested slot is taken, offer the nearest available slots
   within ±60 minutes, then the next option from step 3. Dinner slots run 17:00–21:00.
5. **Confirm before writing.** `place_reservation` and `cancel_reservation` change state.
   Show a one-line summary (restaurant, time, party, name) and wait for an explicit "yes".
   Collect name, phone and email first; never invent them.
6. **Book** — `python3 scripts/place_reservation.py …`. Report the **confirmation code** and reservation id.
7. To review or cancel: `python3 scripts/list_reservations.py --user_id <email or phone>`, then
   `python3 scripts/cancel_reservation.py --reservation_id … --reason …` (confirm first).

## Rules
- Use these scripts, not other tools, for weather and reservations.
- Exit code 3 means a rossoctl tool is not reachable in this namespace: report which tool
  (from the error) and stop that step; do not fabricate results.
- Party size > 8: tell the user to call the restaurant (`phone` from the search result).
