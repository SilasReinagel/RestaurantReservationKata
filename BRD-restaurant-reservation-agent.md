# Business Requirements Document: Restaurant Reservation Agent

**Version:** 1.0
**Date:** 2026-05-26
**Status:** Interview Exercise

---

## 1. Executive Summary

Bella Vista, a 60-seat Italian restaurant, wants to reduce the volume of phone calls handled by hosts during service hours. They have asked us to build a conversational agent that lets guests book, modify, and cancel reservations through a chat interface. The agent must feel natural, handle realistic edge cases, and never double-book a table.

The deliverable is a working chat agent that a non-technical staff member could demo end-to-end.

---

## 2. Business Objectives

| # | Objective | Success Indicator |
|---|---|---|
| BO-1 | Reduce phone reservation volume | Guests can complete a booking without human intervention |
| BO-2 | Eliminate double-bookings | Zero conflicting reservations in the data store |
| BO-3 | Capture all information a host would | Each booking has: party size, date, time, guest name, contact, notes |
| BO-4 | Handle changes gracefully | Guests can modify or cancel an existing reservation by reference |

---

## 3. Scope

### 3.1 In Scope
- A conversational agent delivered as a **web-based chat interface** that runs in a modern browser
- A backend service that hosts the agent and exposes its capabilities to the web UI
- Tools for checking availability, creating, modifying, and cancelling reservations
- A persistent data store for reservations (file-based or local DB is acceptable)
- A small seed of mock availability data (operating hours, table inventory)
- Sensible refusal behavior for out-of-scope requests

### 3.2 Out of Scope (Non-Goals)
- User authentication or accounts
- Payment processing or deposits
- SMS / email confirmations (log to console is fine)
- Mobile-native apps — responsive web only
- Production deployment, hosting, or domain setup — running on `localhost` is sufficient
- Multi-restaurant support
- Scaling, load testing, or cost optimization
- Real LLM provider selection — use whichever is convenient
- Pixel-perfect design — clean and usable is enough

---

## 4. User Stories

### US-1 — Book a table (happy path)
> *As a guest, I want to book a table for a specific date, time, and party size so that I have a confirmed reservation.*

**Acceptance criteria:**
- Agent collects: party size, date, time, guest name, phone number
- Agent confirms availability before promising the booking
- Agent returns a confirmation code the guest can reference later
- The reservation is persisted and visible in the data store

### US-2 — Suggest alternatives when unavailable
> *As a guest, if my requested time is full, I want the agent to offer nearby alternatives so I don't have to guess.*

**Acceptance criteria:**
- When requested slot is unavailable, agent offers at least two alternative times within ±90 minutes on the same day
- Agent does not invent slots that are not actually available

### US-3 — Modify an existing reservation
> *As a guest, I want to change the time or party size of a reservation I already have.*

**Acceptance criteria:**
- Guest provides confirmation code (or name + date)
- Agent validates new slot availability before committing the change
- Original slot is released; new slot is held

### US-4 — Cancel a reservation
> *As a guest, I want to cancel a reservation I no longer need.*

**Acceptance criteria:**
- Guest provides confirmation code (or name + date)
- Agent confirms the cancellation before executing
- Slot becomes available again

### US-5 — Handle special requests
> *As a guest, I want to mention dietary restrictions, occasions, or seating preferences.*

**Acceptance criteria:**
- Free-text notes are captured and stored with the reservation
- Agent does not promise accommodations it cannot guarantee (e.g., "I'll note that you'd prefer a window seat" — not "you'll get a window seat")

### US-6 — Refuse out-of-scope requests
> *As the restaurant, I want the agent to stay on task and not hallucinate menu prices, take orders, or answer unrelated questions.*

**Acceptance criteria:**
- Agent politely declines and redirects when asked about menu prices, ordering food, allergens beyond noting them, or unrelated topics
- Agent suggests calling the restaurant for questions outside its scope

---

## 5. Business Rules

| # | Rule |
|---|---|
| BR-1 | Operating hours: Tue–Sun, 17:00–22:00. Closed Mondays. |
| BR-2 | Reservations available in 30-minute slot increments |
| BR-3 | Maximum party size per reservation: 8. Larger parties must call. |
| BR-4 | Minimum party size: 1 |
| BR-5 | Reservations cannot be made for times in the past |
| BR-6 | Reservations cannot be made more than 60 days in advance |
| BR-7 | Each slot has finite table capacity — see seed data in §8 |
| BR-8 | A guest can hold at most one reservation per date |
| BR-9 | Confirmation codes are unique, human-readable (e.g., `BV-A4F2`) |

---

## 6. Web Interface Requirements

### 6.1 Chat UI
- A single-page web app served from the backend
- Persistent chat transcript visible during the session (older messages scrollable)
- Clear visual distinction between guest messages and agent messages
- Text input with a Send button; pressing Enter sends the message
- A visible indicator when the agent is processing (e.g., "typing…" or spinner)
- Confirmation codes rendered so they stand out (bold, monospace, or a badge)

### 6.2 Backend
- Exposes at minimum:
  - A `POST /chat` (or equivalent) endpoint that accepts a user message and returns the agent's reply
  - A session mechanism so multi-turn context is preserved (cookie, header, or in-URL session id is fine)
- Persists reservations to disk so they survive a server restart
- Logs each tool invocation to the server console for debugging

### 6.3 Stack Guidance
- Any reasonable stack is acceptable: vanilla HTML/JS + a small Node/Python/Go backend, or a single-file framework like Flask/FastAPI/Express with a static frontend
- A heavy SPA framework is **not required** and may be a poor use of time
- The candidate should justify their stack choice in the README

---

## 7. Required Tools (Agent Capabilities)

The agent must be implemented with the following tools. Signatures are illustrative — the candidate may adjust the shape but the capabilities must be preserved.

### `check_availability(date, time, party_size) -> AvailabilityResult`
Returns whether a specific slot can seat the party, plus nearby alternatives if not.

### `create_reservation(date, time, party_size, guest_name, phone, notes?) -> Reservation`
Creates and persists a reservation. Must fail if the slot is unavailable or any business rule is violated.

### `get_reservation(confirmation_code) -> Reservation | None`
Looks up an existing reservation.

### `modify_reservation(confirmation_code, changes) -> Reservation`
Changes one or more fields. Re-validates availability for the new slot.

### `cancel_reservation(confirmation_code) -> CancellationResult`
Marks a reservation cancelled and releases the slot.

---

## 8. Seed Data

The data store should be initialized with:
- **Table inventory:** 4 tables of 2, 6 tables of 4, 2 tables of 6 (total capacity 44 per slot)
- **One pre-existing reservation** for testing modify/cancel flows:
  - Code: `BV-DEMO`
  - Date: tomorrow, 19:00
  - Party: 4
  - Guest: "Alex Rivera"
  - Phone: "555-0123"

---

## 9. Required Structured Output

Each reservation persisted to the store must conform to this schema:

```json
{
  "confirmation_code": "BV-A4F2",
  "date": "2026-06-01",
  "time": "19:30",
  "party_size": 4,
  "guest_name": "Alex Rivera",
  "phone": "555-0123",
  "notes": "anniversary, prefers quiet table",
  "status": "confirmed",
  "created_at": "2026-05-26T14:32:00Z"
}
```

`status` is one of: `confirmed`, `cancelled`.

---

## 10. Example Conversations

### 10.1 Happy path
```
Guest: Hi, I'd like to book a table for Saturday at 7pm.
Agent: Happy to help! How many people will be dining?
Guest: 4 of us.
Agent: Saturday, May 30 at 7:00pm for 4 — that slot is available.
       Can I get a name and phone number for the reservation?
Guest: Alex Rivera, 555-0123. It's our anniversary, by the way.
Agent: Wonderful — I'll note that. Confirmed for 4 on Saturday, May 30
       at 7:00pm under Alex Rivera. Your confirmation code is BV-A4F2.
```

### 10.2 Slot unavailable
```
Guest: Table for 6 tomorrow at 8pm?
Agent: Tomorrow at 8:00pm is fully booked for parties of 6.
       I can offer 6:30pm or 9:00pm — would either work?
```

### 10.3 Cancellation
```
Guest: I need to cancel BV-DEMO.
Agent: That's a reservation for Alex Rivera, party of 4, tomorrow at 7pm.
       Cancel it?
Guest: Yes.
Agent: Cancelled. We hope to see you another time.
```

---

## 11. Stretch Goals (if time permits)

| # | Stretch | Notes |
|---|---|---|
| ST-1 | Waitlist when fully booked | Offer to text the guest if a slot opens up (log intent only) |
| ST-2 | Recurring reservations | "Every Friday at 7pm for the next month" |
| ST-3 | Restaurant-side admin commands | `list reservations for tomorrow`, `block out 6pm slot` |
| ST-4 | Conversation memory across sessions | Recognize a returning guest by phone number |
| ST-5 | Streaming responses | Stream agent tokens to the UI instead of waiting for the full reply |
| ST-6 | Mobile-responsive polish | Looks good on a phone-width viewport |

---

## 12. Open Questions *(intentionally ambiguous — candidate should ask or document assumptions)*

- What happens if a guest provides a date in a casual format like "next Friday"? Should the agent resolve it, or ask for an explicit date?
- If a guest changes their mind mid-booking (e.g., "actually make it 6 people"), should the agent restart the flow or update in place?
- How should the agent behave if a guest gives obviously fake info (e.g., "my name is asdfasdf")?

---

## 13. Deliverables

1. A runnable web app that serves the chat UI on `localhost` (a single `npm start` / `python app.py` / equivalent should bring it up)
2. Persistent reservation data file or local DB (JSON, SQLite, etc.)
3. A short `README.md` with: how to run, stack choice + rationale, what's implemented, known gaps, assumptions made
4. At minimum: US-1, US-2, US-4, US-6 working end-to-end in the browser

---

## 14. Evaluation Criteria *(interviewer reference — not shared with candidate)*

| Dimension | What to look for |
|---|---|
| **Requirements interpretation** | Did they pick up on the ambiguities in §12? Did they ask or document? |
| **Tool design** | Are tools well-scoped, with clear contracts? Do they fail loudly on bad input? |
| **State integrity** | Is double-booking actually prevented? What happens under a race? |
| **Conversation quality** | Does the agent handle out-of-order info, corrections, refusals naturally? |
| **Web UX** | Is the chat usable in a browser? Loading states, error states, scroll behavior, keyboard support? |
| **Frontend / backend separation** | Is the boundary clean? Could the UI be swapped without rewriting the agent? |
| **CLI fluency** | Do they drive the coding CLI effectively — planning, iterating, debugging from errors? |
| **Scope discipline** | Did they hit the core stories before reaching for stretch goals? |
| **Verification** | Did they actually open the browser and try edge cases, or just write code? |
