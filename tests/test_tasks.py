import asyncio
from datetime import UTC, datetime, timedelta

from bot.pray import PrayStore
from bot.tasks import CHECKIN, Done, TaskStore, next_reminder, user_link
from common.state import StateStore
from tests.fakes import FakeCollection

NOW = datetime(2026, 10, 17, 16, 30, tzinfo=UTC)  # 19:30 по UTC+3
TODAY = "2026-10-17"


def make():
    db = StateStore(FakeCollection())
    pray = PrayStore(db)
    return db, pray, TaskStore(db, pray)


def test_toggle_subscription():
    _, _, tasks = make()
    assert tasks.toggle(1, "Эмбер", "amber", CHECKIN.id)
    assert [uid for uid, _ in tasks.subscribers(CHECKIN.id)] == ["1"]
    assert not tasks.toggle(1, "Эмбер", "amber", CHECKIN.id)
    assert tasks.subscribers(CHECKIN.id) == []


def test_complete_once_per_day_and_per_user():
    _, pray, tasks = make()
    tasks.toggle(1, "Эмбер", "amber", CHECKIN.id)
    tasks.toggle(2, "Кэйа", "kaeya", CHECKIN.id)
    assert tasks.complete(1, CHECKIN.id, TODAY, NOW) is Done.OK
    assert tasks.complete(1, CHECKIN.id, TODAY, NOW) is Done.ALREADY
    # награда только нажавшему
    assert pray.user(1)["extra"] == 1
    assert pray.user(2) is None
    assert tasks.complete(2, CHECKIN.id, TODAY, NOW) is Done.OK
    assert pray.user(1)["extra"] == 1 and pray.user(2)["extra"] == 1


def test_complete_rejects_unsubscribed_and_expired():
    _, pray, tasks = make()
    assert tasks.complete(3, CHECKIN.id, TODAY, NOW) is Done.NOT_SUBSCRIBED
    tasks.toggle(1, "Эмбер", "amber", CHECKIN.id)
    # вчерашняя кнопка после полуночи по UTC+3
    assert tasks.complete(1, CHECKIN.id, TODAY, NOW + timedelta(hours=5)) is Done.EXPIRED
    assert tasks.complete(1, CHECKIN.id, "2026-10-18", NOW + timedelta(hours=5)) is Done.OK
    assert pray.user(1)["extra"] == 1


def test_state_persists():
    db, pray, tasks = make()
    tasks.toggle(1, "Эмбер", "amber", CHECKIN.id)
    tasks.complete(1, CHECKIN.id, TODAY, NOW)
    asyncio.run(tasks.save_user(1))
    asyncio.run(pray.save_user(1))
    asyncio.run(tasks.set_reminded(CHECKIN.id, TODAY))
    pray2 = PrayStore(db)
    tasks2 = TaskStore(db, pray2)
    assert tasks2.complete(1, CHECKIN.id, TODAY, NOW) is Done.ALREADY
    assert pray2.user(1)["extra"] == 1
    assert tasks2.reminded_day(CHECKIN.id) == TODAY


def test_next_reminder_at_19_utc3():
    assert next_reminder(datetime(2026, 10, 17, 10, 0, tzinfo=UTC)) == datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
    assert next_reminder(NOW) == datetime(2026, 10, 18, 16, 0, tzinfo=UTC)


def test_user_link_escapes_name():
    assert user_link("5", {"name": "<b>"}) == '<a href="tg://user?id=5">&lt;b&gt;</a>'
    assert user_link("5", {"username": "lisa"}) == '<a href="tg://user?id=5">@lisa</a>'
