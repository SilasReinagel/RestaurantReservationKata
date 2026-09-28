# BRD Traceability: User Story → Acceptance Criterion → Test Case

**Test IDs:** `TC-<story>.<criterion>.<n>` is the pytest function `test_tc_<story>_<criterion>_<n>_…` in
`tests/brd/test_us<story>_*.py`.

**Types:**
- **happy:** the expected path.
- **edge:** a boundary or unusual input.
- **failure:** the system must refuse or recover.
- **prompt-level:** the requirement depends on model behavior, so the test checks the prompt
  instruction. The matching `L-n` live test checks the behavior itself.

**Live tests (`L-n`)** are in `test_live_conversations.py`. They're opt-in (`BV_LIVE_TESTS=1` plus
`OPENAI_API_KEY`) because they call the real model and cost money.

**Manual checks (`MT-n`)** are done in the browser.

Run everything: `python -m pytest tests/brd -v`

---

## US-1: Book a table

| AC | Test case | Type | What it verifies |
|---|---|---|---|
| **AC-1.1** Collects party size, date, time, name, phone | TC-1.1.01 | happy | The create tool requires all five fields, with the right types |
| | TC-1.1.02 | happy | Details collected in conversation are stored exactly as given |
| | TC-1.1.03 | failure | Empty or placeholder names (`""`, `N/A`, `1234`, markup) are rejected |
| | TC-1.1.04 | failure | Placeholder or invalid phone numbers are rejected |
| | TC-1.1.05 | failure | Party sizes 0, −2, 9 and 12 are rejected (BR-3, BR-4) |
| | TC-1.1.06 | edge | Accents, apostrophes, hyphens, single names and international phone formats are accepted |
| | TC-1.1.07 | prompt-level | The prompt says to ask for missing details and never invent them (behavior: L-1) |
| **AC-1.2** Confirms availability before promising | TC-1.2.01 | happy | An open slot reports as available, with a readable "when" |
| | TC-1.2.02 | happy | The first create call is only a preview and saves nothing |
| | TC-1.2.03 | failure | A full slot is never previewed as bookable |
| | TC-1.2.04 | failure | Mondays, outside hours, times off the half hour, the past, more than 60 days ahead, and invalid dates/times are refused (BR-1, 2, 5, 6) |
| | TC-1.2.05 | edge | The slot is taken between the preview and the "yes": the booking is refused, with alternatives |
| | TC-1.2.06 | edge | The slot's start time passes between the preview and the "yes": the booking is refused |
| | TC-1.2.07 | edge | Later today, last seating at 21:30, day 60 and Sunday are all bookable |
| **AC-1.3** Returns a confirmation code | TC-1.3.01 | happy | The code has the `BV-XXXX` readable format (BR-9) |
| | TC-1.3.02 | edge | The code works later in lower case, with spaces, or without the prefix |
| | TC-1.3.03 | edge | 50 bookings get 50 unique codes |
| | TC-1.3.04 | failure | A generated duplicate code is retried |
| | TC-1.3.05 | failure | The guest still gets the code if the model fails after saving |
| | L-1 | live | The code in the reply matches the saved booking, and no codes are made up |
| | MT-1 | manual | The code is shown as a highlighted badge in the chat UI (§6.1) |
| **AC-1.4** Persisted and visible in the data store | TC-1.4.01 | happy | The saved record matches the BRD §9 schema and types |
| | TC-1.4.02 | happy | Book through `/api/chat`, and the booking shows up in `/api/reservations` |
| | TC-1.4.03 | edge | Bookings survive a restart, and the seed isn't applied twice |
| | TC-1.4.04 | failure | 20 guests racing for two 6-tops: exactly 2 bookings succeed (BO-2) |
| | TC-1.4.05 | failure | One booking per guest per date, whatever the phone formatting (BR-8) |

## US-2: Suggest alternatives when unavailable

| AC | Test case | Type | What it verifies |
|---|---|---|---|
| **AC-2.1** Offers alternatives within ±90 min on the same day (D-7: only real ones) | TC-2.1.01 | happy | BRD §10.2 gives 21:00, 18:30 and 21:30, all within ±90 minutes |
| | TC-2.1.02 | happy | Closest first, ties go to the earlier time, at most 4 |
| | TC-2.1.03 | edge | Only one real option: just that one is offered |
| | TC-2.1.04 | edge | No real options: an empty list, never padded |
| | TC-2.1.05 | edge | Never before 17:00 or after last seating |
| | TC-2.1.06 | edge | Never a time that has already passed today |
| | TC-2.1.07 | edge | A request for 19:15 suggests 19:00 and 19:30 |
| | TC-2.1.08 | failure | A failed booking hands the alternatives to the agent |
| | TC-2.1.09 | edge | Alternatives depend on the party size |
| **AC-2.2** Never invents slots | TC-2.2.01 | happy | Every offered alternative passes a real check and a dry-run booking |
| | TC-2.2.02 | edge | An alternative has to be free for the full 2-hour hold |
| | TC-2.2.03 | edge | No alternatives are returned when the requested slot is available |
| | TC-2.2.04 | happy | Accepting an offered alternative really books it |
| | TC-2.2.05 | prompt-level | The prompt forbids offering times the tools didn't return (behavior: L-2) |

## US-3: Modify an existing reservation

| AC | Test case | Type | What it verifies |
|---|---|---|---|
| **AC-3.1** Guest provides the code (D-4: code only) | TC-3.1.01 | happy | BV-DEMO can be modified by code in any format |
| | TC-3.1.02 | failure | An unknown code is reported |
| | TC-3.1.03 | failure | There's no lookup by name + date (D-4) |
| | TC-3.1.04 | failure | A cancelled booking can't be modified |
| | TC-3.1.05 | failure | A booking that has started can't be modified |
| **AC-3.2** Validates the new slot before committing | TC-3.2.01 | happy | A preview leaves the booking untouched; the "yes" commits it |
| | TC-3.2.02 | happy | Changing the party size re-seats: 8 guests get 2 tables, a smaller party gets a smaller table |
| | TC-3.2.03 | failure | An unavailable new slot leaves the original booking and its tables unchanged |
| | TC-3.2.04 | failure | Every business rule applies to the new values |
| | TC-3.2.05 | failure | Moving onto a date where the guest already has a booking is refused (BR-8) |
| | TC-3.2.06 | edge | No actual change is reported as such |
| | TC-3.2.07 | edge | The slot is taken between the preview and the "yes": the original booking survives |
| | TC-3.2.08 | edge | Changing the request after a preview needs a new confirmation |
| **AC-3.3** Old slot released, new slot held | TC-3.3.01 | happy | The old slot opens up and the new slot is held |
| | TC-3.3.02 | edge | A 30-minute move that overlaps its own hold is allowed |
| | TC-3.3.03 | edge | Shrinking from 8 to 4 frees the extra table |
| | TC-3.3.04 | edge | Moving to another date releases the old date |
| | TC-3.3.05 | happy | Moving BV-DEMO frees 19:00 |
| | L-7 | live | "Move BV-DEMO to 8:30pm" → "yes" → changed |

## US-4: Cancel a reservation

| AC | Test case | Type | What it verifies |
|---|---|---|---|
| **AC-4.1** Guest provides the code (D-4: code only) | TC-4.1.01 | happy | BRD §10.3 cancels BV-DEMO: read back, "yes", cancelled |
| | TC-4.1.02 | edge | The code works in any format |
| | TC-4.1.03 | failure | An unknown code is reported |
| **AC-4.2** Confirms before executing | TC-4.2.01 | failure | Cancelling without looking the booking up first is refused |
| | TC-4.2.02 | failure | Cancelling in the same turn as the lookup is refused |
| | TC-4.2.03 | edge | The guest declines, and nothing changes |
| | TC-4.2.04 | edge | A lookup the guest never saw (model outage) doesn't count as confirmation |
| | TC-4.2.05 | failure | Cancelling an already-cancelled booking is reported |
| | TC-4.2.06 | failure | A past booking can't be cancelled |
| | L-3 | live | BRD §10.3 end to end with the real model |
| **AC-4.3** Slot becomes available again | TC-4.3.01 | happy | Another guest can book the cancelled slot |
| | TC-4.3.02 | edge | The record is kept with `status: cancelled` |
| | TC-4.3.03 | edge | The same guest can book the same date again |
| | TC-4.3.04 | edge | Cancelling a party that used combined tables frees both tables |

## US-5: Handle special requests

| AC | Test case | Type | What it verifies |
|---|---|---|---|
| **AC-5.1** Notes captured and stored | TC-5.1.01 | happy | Dietary, occasion, seating and access requests are stored |
| | TC-5.1.02 | happy | The note is shown in the preview the guest confirms |
| | TC-5.1.03 | edge | Missing notes become empty; extra spaces are trimmed |
| | TC-5.1.04 | edge/failure | 500 characters are accepted; 501 are rejected |
| | TC-5.1.05 | edge | Unicode, emoji and markup are stored exactly as given |
| | TC-5.1.06 | edge | A re-worded note at confirmation is saved without asking again |
| | TC-5.1.07 | happy | Notes can be added later with a notes-only change |
| | TC-5.1.08 | edge | Notes can be cleared |
| **AC-5.2** No promised accommodations | TC-5.2.01 | edge | A seating note doesn't change the table assignment |
| | TC-5.2.02 | edge | No tool can reserve a specific table or seating area |
| | TC-5.2.03 | prompt-level | "I'll note that", never "you'll get" (behavior: L-6) |

## US-6: Refuse out-of-scope requests

| AC | Test case | Type | What it verifies |
|---|---|---|---|
| **AC-6.1** Declines menu prices, orders, allergen advice, unrelated topics | TC-6.1.01 | happy | Only the 5 reservation tools exist |
| | TC-6.1.02 | prompt-level | The prompt defines the out-of-scope topics (behavior: L-4, L-5, L-8) |
| | TC-6.1.03 | failure | A tool the model makes up gets a safe error |
| | TC-6.1.04 | failure | Lookups can't list other guests, even with wildcards or injection strings |
| | TC-6.1.05 | edge | A declined off-topic turn changes no data |
| **AC-6.2** Suggests calling the restaurant | TC-6.2.01 | happy | The prompt gives the restaurant's number for redirects |
| | TC-6.2.02 | failure | Parties over 8 are told to call (BR-3) |
| | TC-6.2.03 | failure | The outage message includes the number |
| | TC-6.2.04 | edge | The tool-round limit ends with the number |
| | TC-6.2.05 | prompt-level | Changes without a code are redirected to the phone number (D-4) |
