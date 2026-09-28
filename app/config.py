"""Business constants and environment-driven settings.

All business rules live in code (see rules.py); these constants are the single
place where the numbers come from.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Business constants (BRD §5 + agreed decisions D-1..D-7) ---------------

OPEN_WEEKDAYS = {1, 2, 3, 4, 5, 6}  # Tue..Sun (Monday == 0 is closed)
OPENING_MIN = 17 * 60               # 17:00
CLOSING_MIN = 22 * 60               # 22:00
LAST_SEATING_MIN = 21 * 60 + 30     # 21:30 (assumption A-1: tables may be held past close)
SLOT_MINUTES = 30
HOLD_MINUTES = 120                  # D-1: a reservation holds its table(s) for 2 hours
MIN_PARTY = 1
MAX_PARTY = 8
MIN_COMBINED_PARTY = 7              # D-2: only parties of 7-8 may combine tables
MAX_DAYS_AHEAD = 60
ALTERNATIVE_WINDOW_MINUTES = 90
MAX_ALTERNATIVES = 4
MAX_NOTES_LENGTH = 500
MAX_MESSAGE_LENGTH = 1000

# 4 x 2-tops, 6 x 4-tops, 2 x 6-tops (BRD §8)
TABLE_INVENTORY = (
    [(f"T2-{i}", 2) for i in range(1, 5)]
    + [(f"T4-{i}", 4) for i in range(1, 7)]
    + [(f"T6-{i}", 6) for i in range(1, 3)]
)

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    timezone: ZoneInfo
    restaurant_phone: str
    openai_model: str
    openai_api_key: str | None
    reasoning_effort: str | None
    seed_demo_scenarios: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=Path(os.getenv("BV_DB_PATH", ROOT_DIR / "data" / "bella_vista.db")),
            timezone=ZoneInfo(os.getenv("BV_TIMEZONE", "America/New_York")),
            restaurant_phone=os.getenv("BV_RESTAURANT_PHONE", "(555) 010-0100"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT") or None,
            seed_demo_scenarios=os.getenv("BV_SEED_DEMO_SCENARIOS", "true").lower() == "true",
        )
