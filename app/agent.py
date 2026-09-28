"""Conversational agent: system prompt, tool schemas, dispatch, and the tool-calling loop.

The LLM only collects details and phrases replies. Every rule is enforced by
ReservationService; the prompt describes rules only so the agent can explain them.
"""

import json
import logging
import threading
import time as time_module
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from app import config
from app.reservations import ReservationService
from app.rules import ReservationError, describe_slot, normalize_code, normalize_phone, parse_date, parse_time

log = logging.getLogger("bella_vista.tools")

MAX_TOOL_ROUNDS = 8

# A completion function takes (messages, tools) and returns
# {"content": str | None, "tool_calls": [{"id", "name", "arguments"}], "finish_reason": str}.
CompleteFn = Callable[[list[dict], list[dict]], dict]


class LLMUnavailable(Exception):
    """The model could not be reached and nothing was changed this turn."""


# --- Prompt ------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the reservation assistant for Bella Vista, an Italian restaurant. You help guests \
book, change, and cancel table reservations through chat. Be warm, natural, and brief.

How to work:
- Use the tools for every fact about availability and reservations. Never state that a time is available, \
never offer a time, and never give a confirmation code unless a tool result in this conversation says so.
- The tools enforce all booking rules. If a tool returns ok=false, explain the message to the guest in plain \
words and use any "alternatives" it returns. If there are fewer than two alternatives, offer only what exists; \
if there are none, say so and suggest another day or calling the restaurant.
- Convert the guest's dates and times to YYYY-MM-DD and 24-hour HH:MM using the calendar below. When you \
confirm details, repeat the weekday and date explicitly (for example "Saturday, October 3 at 7:00 PM"); prefer \
the "when" text from tool results. If a date phrase is genuinely ambiguous, ask.
- To book you need: party size, date, time, guest name, and phone number. Ask for whatever is missing, a little \
at a time. Never invent or guess guest details. If the guest corrects something mid-conversation, update it and \
carry on without restarting.
- create_reservation and modify_reservation work in two steps. As soon as you have all the details, call the \
tool: the first call saves nothing and returns CONFIRMATION_REQUIRED with a "preview". Read the preview back and \
ask the guest to confirm. After they say yes in their next message, call the tool again with the same details to \
save it. Don't ask for confirmation separately before the first call; the preview is what the guest confirms.
- To cancel: look the reservation up with get_reservation, read back the details, and ask the guest to confirm. \
Only call cancel_reservation after they say yes in a later message.
- Changes and cancellations require the confirmation code. If the guest doesn't have it, explain that and \
suggest calling the restaurant.
- Special requests (dietary needs, occasions, seating preferences) go in the notes. Say you'll note them; never \
promise they will be accommodated.
- If a name looks obviously fake, politely ask once for the name the reservation should be under, then accept \
the answer.

Out of scope: menu items, prices, taking food orders, allergen or ingredient advice (you may only note an allergy), \
and anything unrelated to reservations. Politely decline and suggest calling the restaurant at {phone}. Parties \
larger than {max_party} must call the restaurant.

Restaurant facts: open Tuesday-Sunday, closed Mondays. Reservations start every 30 minutes from 17:00 to 21:30 \
and hold the table for 2 hours. Bookings open up to {max_days} days ahead. Phone: {phone}.

Current date and time at the restaurant: {now}.
Calendar for the next 14 days:
{calendar}
"""


def build_system_prompt(now: datetime, phone: str) -> str:
    lines = []
    for offset in range(15):
        day = now.date() + timedelta(days=offset)
        label = "today" if offset == 0 else "tomorrow" if offset == 1 else ""
        status = "open" if day.weekday() in config.OPEN_WEEKDAYS else "CLOSED"
        lines.append(f"- {day.isoformat()} {day.strftime('%A')} {status} {label}".rstrip())
    return SYSTEM_PROMPT.format(
        phone=phone,
        max_party=config.MAX_PARTY,
        max_days=config.MAX_DAYS_AHEAD,
        now=now.strftime("%A %Y-%m-%d %H:%M %Z"),
        calendar="\n".join(lines),
    )


# --- Tool schemas (OpenAI strict mode: every property required; optional => nullable) ---


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


_DATE = {"type": "string", "description": "Date in YYYY-MM-DD format."}
_TIME = {"type": "string", "description": "Start time in 24-hour HH:MM format, e.g. 19:30."}
_PARTY = {"type": "integer", "description": "Number of guests."}
_CODE = {"type": "string", "description": "Confirmation code, e.g. BV-A4F2."}

TOOLS = [
    _tool(
        "check_availability",
        "Check whether a table is free for a party at a date and time. Returns real alternatives when it is not.",
        {"date": _DATE, "time": _TIME, "party_size": _PARTY},
    ),
    _tool(
        "create_reservation",
        "Create a reservation. The first call validates and returns a preview without saving; call again "
        "with the same details after the guest confirms in a later message to save it.",
        {
            "date": _DATE,
            "time": _TIME,
            "party_size": _PARTY,
            "guest_name": {"type": "string", "description": "Name the reservation is under."},
            "phone": {"type": "string", "description": "Guest phone number."},
            "notes": {
                "type": ["string", "null"],
                "description": "Special requests (dietary needs, occasion, seating preference), or null.",
            },
        },
    ),
    _tool("get_reservation", "Look up a reservation by confirmation code.", {"confirmation_code": _CODE}),
    _tool(
        "modify_reservation",
        "Change an existing reservation. Pass null for fields that are not changing. The first call validates "
        "and returns a preview without saving; call again with the same details after the guest confirms.",
        {
            "confirmation_code": _CODE,
            "date": {**_DATE, "type": ["string", "null"]},
            "time": {**_TIME, "type": ["string", "null"]},
            "party_size": {**_PARTY, "type": ["integer", "null"]},
            "notes": {"type": ["string", "null"], "description": "Replacement notes, or null to keep them."},
        },
    ),
    _tool(
        "cancel_reservation",
        "Cancel a reservation. Only after get_reservation was used and the guest confirmed in a later message.",
        {"confirmation_code": _CODE},
    ),
]

MUTATING_TOOLS = {"create_reservation", "modify_reservation", "cancel_reservation"}


# --- Sessions -----------------------------------------------------------------------------


@dataclass
class PendingAction:
    """A create/modify that passed a dry run and is waiting for the guest's confirmation."""

    tool: str
    key: tuple  # the fields the guest confirms, normalized
    turn: int   # the turn in which the preview was shown


@dataclass
class Session:
    id: str
    messages: list[dict] = field(default_factory=list)  # conversation history, no system prompt
    turn: int = 0
    looked_up: dict[str, int] = field(default_factory=dict)  # confirmation code -> turn it was read back
    pending: PendingAction | None = None  # at most one proposal awaiting confirmation
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None) -> tuple[Session, bool]:
        """Returns (session, was_reset). was_reset is True when a known-looking id was not found."""
        with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id], False
            session = Session(id=uuid.uuid4().hex)
            self._sessions[session.id] = session
            return session, bool(session_id)


# --- Tool dispatch ------------------------------------------------------------------------------


def _with_when(record: dict) -> dict:
    return {**record, "when": describe_slot(parse_date(record["date"]), parse_time(record["time"]))}


class ToolDispatcher:
    def __init__(self, service: ReservationService):
        self.service = service

    def run(self, session: Session, name: str, raw_arguments: str) -> dict:
        started = time_module.perf_counter()
        try:
            args = json.loads(raw_arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            result = {"ok": False, "error_code": "INVALID_ARGUMENTS", "message": f"Could not parse arguments: {exc}"}
            self._log(session, name, raw_arguments, result, started)
            return result
        try:
            result = self._execute(session, name, args)
        except ReservationError as err:
            result = err.to_dict()
        except (TypeError, KeyError) as exc:
            result = {"ok": False, "error_code": "INVALID_ARGUMENTS", "message": str(exc)}
        self._log(session, name, args, result, started)
        return result

    def _execute(self, session: Session, name: str, args: dict) -> dict:
        s = self.service
        if name == "check_availability":
            return s.check_availability(args["date"], args["time"], args["party_size"])
        if name == "create_reservation":
            create_args = (
                args["date"], args["time"], args["party_size"], args["guest_name"], args["phone"], args.get("notes")
            )
            preview = s.create(*create_args, dry_run=True)
            key = (preview["date"], preview["time"], preview["party_size"],
                   preview["guest_name"].casefold(), normalize_phone(preview["phone"]))
            self._require_confirmation(session, name, key, preview)
            record = s.create(*create_args)
            return {"ok": True, "reservation": _with_when(record)}
        if name == "get_reservation":
            record = s.get(args["confirmation_code"])
            session.looked_up[record["confirmation_code"]] = session.turn
            return {"ok": True, "reservation": _with_when(record)}
        if name == "modify_reservation":
            changes = dict(
                date_str=args.get("date"),
                time_str=args.get("time"),
                party_size=args.get("party_size"),
                notes=args.get("notes"),
            )
            preview = s.modify(args["confirmation_code"], **changes, dry_run=True)
            key = (preview["confirmation_code"], preview["date"], preview["time"], preview["party_size"])
            self._require_confirmation(session, name, key, preview)
            record = s.modify(args["confirmation_code"], **changes)
            return {"ok": True, "reservation": _with_when(record)}
        if name == "cancel_reservation":
            code = normalize_code(args["confirmation_code"])
            # Enforced in code, not the prompt: the guest must have seen the details in an earlier turn.
            if session.looked_up.get(code, session.turn) >= session.turn:
                raise ReservationError(
                    "CONFIRMATION_REQUIRED",
                    "Look the reservation up with get_reservation, read the details back, and wait for the "
                    "guest to confirm in their next message before cancelling.",
                )
            record = s.cancel(code)
            return {"ok": True, "reservation": _with_when(record)}
        return {"ok": False, "error_code": "UNKNOWN_TOOL", "message": f"Unknown tool {name}."}

    @staticmethod
    def _require_confirmation(session: Session, tool: str, key: tuple, preview: dict) -> None:
        """Let a create/modify through only if the same proposal was previewed in an earlier turn.

        Notes are deliberately not part of the key: they're non-binding requests, and models often
        re-word them between calls, which would otherwise force the guest to confirm again.
        """
        pending = session.pending
        same = pending is not None and pending.tool == tool and pending.key == key
        if same and pending.turn < session.turn:
            session.pending = None
            return
        if not same:  # new or changed proposal; a same-turn repeat keeps its original turn
            session.pending = PendingAction(tool, key, session.turn)
        raise ReservationError(
            "CONFIRMATION_REQUIRED",
            "Nothing has been saved yet. Read this preview back to the guest and ask them to confirm. "
            "After they say yes in their next message, call this tool again with the same details.",
            preview=_with_when(preview),
        )

    @staticmethod
    def _log(session: Session, name: str, args, result: dict, started: float) -> None:
        elapsed = (time_module.perf_counter() - started) * 1000
        outcome = "ok" if result.get("ok") else f"error={result.get('error_code')}"
        code = (result.get("reservation") or {}).get("confirmation_code")
        log.info(
            "[tool] session=%s turn=%d %s args=%s -> %s%s (%.0fms)",
            session.id[:8], session.turn, name, json.dumps(args, default=str), outcome,
            f" code={code}" if code else "", elapsed,
        )


# --- The loop -----------------------------------------------------------------------------------------


def _fallback_after_mutation(name: str, result: dict) -> str:
    """Used when a change was saved but the model could not produce the final reply."""
    r = result["reservation"]
    if name == "cancel_reservation":
        return f"Your reservation {r['confirmation_code']} has been cancelled."
    verb = "updated" if name == "modify_reservation" else "confirmed"
    return (
        f"Your reservation is {verb}: party of {r['party_size']} on {r['when']} under {r['guest_name']}. "
        f"Your confirmation code is {r['confirmation_code']}."
    )


class Agent:
    def __init__(self, service: ReservationService, complete: CompleteFn | None, restaurant_phone: str):
        self.service = service
        self.complete = complete
        self.phone = restaurant_phone
        self.dispatcher = ToolDispatcher(service)

    def handle(self, session: Session, user_text: str) -> str:
        """Run one guest turn. History is only committed when the turn finishes consistently."""
        if self.complete is None:
            raise LLMUnavailable("The assistant is not configured (missing OPENAI_API_KEY).")
        session.turn += 1
        turn_messages: list[dict] = [{"role": "user", "content": user_text}]
        last_mutation: tuple[str, dict] | None = None

        for _ in range(MAX_TOOL_ROUNDS):
            system = {"role": "system", "content": build_system_prompt(self.service.clock(), self.phone)}
            try:
                response = self.complete([system, *session.messages, *turn_messages], TOOLS)
            except Exception as exc:  # network, auth, rate limit, provider outage
                log.warning("LLM call failed: %s", exc)
                self._drop_unseen_proposal(session)
                if last_mutation:
                    return self._finish(session, turn_messages, _fallback_after_mutation(*last_mutation))
                raise LLMUnavailable("The assistant is temporarily unavailable.") from exc

            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                if response.get("finish_reason") in ("length", "content_filter") or not response.get("content"):
                    self._drop_unseen_proposal(session)
                    text = (
                        _fallback_after_mutation(*last_mutation) if last_mutation
                        else "Sorry, I didn't catch that. Could you rephrase?"
                    )
                else:
                    text = response["content"]
                return self._finish(session, turn_messages, text)

            turn_messages.append({
                "role": "assistant",
                "content": response.get("content"),
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in tool_calls
                ],
            })
            for call in tool_calls:
                result = self.dispatcher.run(session, call["name"], call["arguments"])
                if call["name"] in MUTATING_TOOLS and result.get("ok"):
                    last_mutation = (call["name"], result)
                turn_messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})

        log.warning("Tool round limit reached in session %s", session.id[:8])
        self._drop_unseen_proposal(session)
        text = (
            _fallback_after_mutation(*last_mutation) if last_mutation
            else f"Sorry, I'm having trouble with that request. Please try rephrasing, or call us at {self.phone}."
        )
        return self._finish(session, turn_messages, text)

    @staticmethod
    def _drop_unseen_proposal(session: Session) -> None:
        """A preview or lookup made this turn only counts if the model's reply (which reads it back)
        reached the guest; otherwise it must not authorize a create/modify/cancel next turn."""
        if session.pending and session.pending.turn == session.turn:
            session.pending = None
        for code in [c for c, turn in session.looked_up.items() if turn == session.turn]:
            del session.looked_up[code]

    @staticmethod
    def _finish(session: Session, turn_messages: list[dict], text: str) -> str:
        turn_messages.append({"role": "assistant", "content": text})
        session.messages.extend(turn_messages)
        return text


# --- OpenAI adapter ---------------------------------------------------------------------------------------


def openai_complete(api_key: str, model: str, reasoning_effort: str | None = None, http_client=None) -> CompleteFn:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2, http_client=http_client)

    def complete(messages: list[dict], tools: list[dict]) -> dict:
        extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            **extra,
        )
        choice = response.choices[0]
        message = choice.message
        return {
            "content": message.content or getattr(message, "refusal", None),
            "tool_calls": [
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                for c in (message.tool_calls or [])
            ],
            "finish_reason": choice.finish_reason,
        }

    return complete
