from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.reservations import ReservationService

TZ = ZoneInfo("America/New_York")
# Tuesday 2026-09-29, noon restaurant time. Tomorrow (the demo day) is Wednesday 2026-09-30.
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=TZ)


class MutableClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return MutableClock(NOW)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


@pytest.fixture
def service(db_path, clock):
    return ReservationService(db_path, clock)


def book(service, date="2026-10-03", time="19:00", party=4, name="Alex Rivera", phone="555-0100", notes=""):
    return service.create(date, time, party, name, phone, notes)
