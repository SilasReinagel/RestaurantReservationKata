# Bella Vista Reservation Agent

A chat agent that books, modifies, and cancels reservations for Bella Vista (a 60-seat Italian
restaurant, BRD v1.0). It's a Python backend with a vanilla-JS chat UI, running on `localhost`.

## 1. How to run it

### Prerequisites

- Python 3.11+ (developed on 3.14, Windows)
- An OpenAI API key with access to a tool-calling chat model (default `gpt-5-mini`)

### Setup and start

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
                                  # then set OPENAI_API_KEY (and OPENAI_MODEL if needed)
python run.py                     # open http://127.0.0.1:8000
```

### Useful commands

| Command | What it does |
|---|---|
| `python run.py` | Starts the server on `127.0.0.1:8000`. The database is created and seeded on first start. |
| `python run.py --port 8080` | Starts on a different port. |
| `python run.py --reset` | Wipes the database and seeds the demo data again. Use it after a demo that cancels `BV-DEMO`, or when the demo day has passed. |
| `python -m pytest -q` | Runs the test suite (257 tests, about 5 s). Needs no API key. |
| `set BV_LIVE_TESTS=1 && python -m pytest tests/brd/test_live_conversations.py -v` | Runs the 8 opt-in live tests against the real model. Needs `OPENAI_API_KEY` and costs API credits. |
| `GET /api/reservations?date=YYYY-MM-DD` | Shows what's in the data store. For demos only; it has no authentication. |

Without `OPENAI_API_KEY` the UI still loads, and chat returns a clear "not configured" message.

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for chat |
| `OPENAI_MODEL` | `gpt-5-mini` | Any tool-calling chat model |
| `OPENAI_REASONING_EFFORT` | empty | Optional, reasoning models only: `minimal` / `low` / `medium` / `high` |
| `BV_TIMEZONE` | `America/New_York` | Restaurant local time |
| `BV_RESTAURANT_PHONE` | `(555) 010-0100` | Phone number the agent gives out |
| `BV_DB_PATH` | `data/bella_vista.db` | SQLite file location |
| `BV_SEED_DEMO_SCENARIOS` | `true` | Seeds two filler bookings so BRD §10.2 is reproducible |

### Demo script

The seed data puts `BV-DEMO` (Alex Rivera, 4 guests, 19:00) on the next open day, which is
"tomorrow" unless tomorrow is a Monday. It also adds two filler bookings on the same day so BRD §10.2
can be reproduced.

1. **Book:** "Table for 2 this Saturday at 7pm", then give a name and phone number. The agent reads
   back a preview; say "yes" to save it.
2. **Alternatives:** "Table for 6 tomorrow at 8pm?" It's full; the real options are 9:00pm, 6:30pm
   and 9:30pm.
3. **Modify:** "Can I move BV-DEMO to 8pm?"
4. **Cancel:** "I need to cancel BV-DEMO", then "yes".
5. **Out of scope:** "How much is the lasagna?" or "Table for 12".

> **Seed phone number conflict:** `BV-DEMO` uses the phone number `555-0123` required by BRD §8, and
> BRD §10.1 uses the same guest and number. Because of the one-booking-per-phone-per-date rule,
> booking with `555-0123` **on the demo day** is rejected ("already has a reservation on that date").
> That's correct behavior, not a bug. Booking with that number on any other day works. For a clean
> happy-path demo, use a different number (e.g. `555-0199`) or a different day. The two filler
> bookings (`BV-SEDA`, `BV-SEDB`, notes "demo seed data") also mean parties of 5–6 can't be seated
> between 17:30 and 21:00 on the demo day. Set `BV_SEED_DEMO_SCENARIOS=false` to leave them out.

## 2. What's implemented

### Features

- **Book a table** (US-1): collects date, time, party size, name and phone; reads back a preview;
  saves only after the guest says yes; returns a readable confirmation code.
- **Alternatives when full** (US-2): offers only real, bookable slots within ±90 minutes on the same
  day, even if that's fewer than two.
- **Modify** (US-3): change the date, time, party size or notes of an existing booking by code.
- **Cancel** (US-4): looks up the booking, shows the details, cancels only after a separate "yes".
- **Special requests** (US-5): stored as notes, acknowledged as "I'll note that" (non-binding).
- **Out-of-scope handling** (US-6): polite refusals for menu, pricing, large parties, etc., with the
  restaurant phone number.
- **Real table allocation:** 2-hour holds per table, smallest-fit seating, table combining for
  parties of 7–8, and a double-booking guarantee enforced in a database transaction.
- **Chat UI:** a single static page with no build step.

The agent exposes five tools to the model: `check_availability`, `get_reservation`,
`create_reservation`, `modify_reservation` and `cancel_reservation`.

### Stack and why

| Choice | Why |
|---|---|
| FastAPI + Uvicorn | Small, typed request validation, serves the static UI, easy `TestClient` |
| SQLite (stdlib `sqlite3`) | A file-based store the BRD allows, with real transactions and constraints that give the double-booking guarantee |
| Vanilla HTML/JS/CSS | No build step; the BRD says a heavy SPA isn't needed |
| OpenAI Chat Completions + function calling | Strict JSON-schema tools; a hand-written loop of about 80 lines that's easy to test with a fake model. No agent framework. |

### Project structure

```
run.py                   entry point: loads .env, --reset / --port, starts Uvicorn
requirements.txt         runtime + test dependencies
.env.example             configuration template
BRD-restaurant-reservation-agent.md   the requirements document

app/
  config.py              business constants (hours, 30-min slots, 2-hour hold, limits) + env settings
  rules.py               pure rules: validation, slot math, table allocation, code/phone/name handling
  db.py                  SQLite connection, schema, BEGIN IMMEDIATE transactions
  reservations.py        ReservationService: check/create/get/modify/cancel + demo seed
  agent.py               system prompt, tool schemas, tool dispatch, conversation loop, OpenAI adapter
  main.py                FastAPI routes: GET /, POST /api/chat, GET /api/reservations

static/
  index.html, app.js, styles.css   chat UI

tests/
  test_rules.py          pure rule functions
  test_reservations.py   service layer, including a concurrent double-booking race test
  test_agent.py          conversation loop and confirmation gates with a fake LLM
  test_api.py            HTTP routes
  test_openai_adapter.py OpenAI request/response mapping
  brd/                   one file per BRD user story (US-1 to US-6), plus opt-in live tests
  brd/TRACEABILITY.md    user story → acceptance criterion → test case map

data/                    SQLite database (created on first run)
```

### Where each rule is enforced

The model collects details and writes the replies. **Code decides.**

| Rule | Enforced in |
|---|---|
| Open Tue–Sun, starts every 30 min 17:00–21:30, not in the past, ≤ 60 days ahead | `rules.validate_slot` |
| Party size 1–8 | `rules.validate_party_size` |
| A table can't be double-booked (2-hour hold per table) | `ReservationService`, inside a `BEGIN IMMEDIATE` transaction |
| One confirmed reservation per phone number per date (BR-8) | Service check, plus a partial unique index in SQLite |
| Unique, readable confirmation codes | `rules.generate_code` + UNIQUE column + retry |
| Alternatives are real and within ±90 min on the same day | `ReservationService._alternatives` |
| Confirm before create/modify | `ToolDispatcher`: the first call does a full validated dry run, saves nothing, and records a pending proposal in the session. Only the **same** proposal repeated in a **later** guest turn is saved. |
| Cancel only after the guest has seen the details | `ToolDispatcher`: `cancel_reservation` is refused unless `get_reservation` returned that code in an **earlier** turn |
| Tone, refusals, "I'll note that" wording | Prompt only |

**How the create/modify confirmation works**

- **The preview is a dry run of the real write.** `create`/`modify` run with `dry_run=True`: the same
  checks in the same transaction, stopping before any write. A preview can't promise something the
  real call would reject.
- **What the guest confirms** is date, time, party size, name (ignoring case) and phone (digits only);
  for a modify, it's the code plus the resulting date, time and party size.
  - **Notes aren't part of the match.** Models often re-word them between calls ("anniversary" →
    "Anniversary dinner"), which would make the guest confirm again. Notes are non-binding, and the
    latest wording is saved.
- **Changing any confirmed field** creates a new proposal that needs its own "yes". Repeating the
  call in the same turn never saves.
- **A preview or reservation lookup from a turn whose reply never reached the guest is discarded,**
  so it can't authorize a create, modify or cancel on the next turn. That covers a model
  error, a truncated reply, or the round limit.

### Decisions and assumptions

Stakeholder decisions: tables are held for 2 hours; availability is based on real tables; parties of
7–8 may combine tables; the restaurant phone number is a placeholder `(555) 010-0100`; changes and
cancellations need the confirmation code only; all times are the restaurant's local time; modify is
required; only real alternatives are offered, even if that's fewer than two.

Assumptions I made. Each one is a single constant or function if it needs to change.

- **Last seating is 21:30.** Tables may be held past the 22:00 close; 22:00 itself isn't bookable.
- **Seating:** parties of 1–6 get the smallest single table that fits, falling back to a larger
  table. Parties of 7–8 get the pair of tables with the fewest total seats. When two pairs have the
  same seats, the pair whose larger table is smaller wins, so 4+4 beats 2+6 and 6-tops stay free
  for parties of 5–6, which can't combine. Any two tables can be combined, and there are no
  3-table combinations.
- **BR-8 identity** is the phone number, compared as digits only. Family members sharing a phone
  number can't book separately on the same night.
- **Modify** can change the date, time, party size and notes. A name or phone number change means
  cancelling and rebooking.
- **Phone numbers** have 7–15 digits, so the BRD's `555-0123` is valid. Names must contain letters.
  Notes are limited to 500 characters and messages to 1,000.
- **Casual dates** ("next Friday") are resolved by the model using a 14-day calendar in the prompt,
  always read back as an explicit weekday and date, and validated by the tools.
- **Mid-booking corrections** update the details in place rather than restarting.
- **Fake-looking names:** the agent asks once, then accepts the answer. Clearly invalid input is
  rejected by the tools.
- **The "60-seat restaurant" vs 44 reservable seats:** the difference is assumed to be bar or
  walk-in seating that can't be reserved.
- **The BV-DEMO seed** moves to Tuesday when "tomorrow" would be a Monday. The BRD's schema example
  date (2026-06-01) is a Monday and is treated as illustrative only.
- **`created_at`** is stored in UTC; `date`/`time` are restaurant local time (`BV_TIMEZONE`,
  default `America/New_York`).

## 3. Future improvements

Each item starts from a current limitation.

1. **Stronger confirmation system**
   * Add SMS or email confirmation for new bookings, modifications, and cancellations.
   * This would provide stronger identity verification and reduce the risk of someone using only a confirmation code.

2. **Booking reminders and availability notifications**

   * Send SMS or email reminders with the reservation details before the booking.
   * If a requested time is unavailable, allow the guest to join a waitlist.
   * Notify the guest when a table becomes available, for example after another reservation is cancelled.

3. **Persistent chat sessions**

   * Store conversation sessions in SQLite or Redis so conversations can survive server restarts.

4. **Smarter table assignment**

   * Improve table allocation to better optimize the whole evening, especially when handling larger parties.

5. **Staff/admin features**

   * Add authenticated staff access for viewing reservations, blocking tables, changing hours, and managing bookings.

6. **Streaming responses**

   * Stream agent responses so guests can see the reply as it is generated.

7. **Production hardening**

   * Move to PostgreSQL for multi-instance deployments.
   * Add structured logging, metrics, rate limiting, and more live-model testing.

8. **Additional booking features**

   * Support recurring reservations, SMS/email confirmations, reminders, and other future reservation workflows.

