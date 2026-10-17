import asyncio
from datetime import UTC, datetime, timedelta

from bot.pray import PrayStore
from bot.tasks import CHECKIN, Done, TaskStore, next_reminder
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


def test_all_done_today():
    _, _, tasks = make()
    assert not tasks.all_done_today(1, NOW)  # ни на что не записан
    tasks.toggle(1, "Эмбер", "amber", CHECKIN.id)
    assert not tasks.all_done_today(1, NOW)
    tasks.complete(1, CHECKIN.id, TODAY, NOW)
    assert tasks.all_done_today(1, NOW)
    assert not tasks.all_done_today(1, NOW + timedelta(hours=5))  # новые сутки


def test_alert_texts_fit_telegram_limit():
    from bot import texts
    limits = [t.format(user="@very_long_username_xx", wait="10 ч 59 мин")
              for t in (*texts.PRAY_LIMIT_INVITE, *texts.PRAY_LIMIT_TASK_PENDING, *texts.PRAY_LIMIT_TASKS_DONE)]
    alerts = [*texts.TASK_ALREADY, texts.TASK_NOT_SUBSCRIBED, texts.TASK_EXPIRED, texts.TASKS_NOT_YOURS,
              texts.TASK_REWARD.format(reward=1, left=99), *limits,
              texts.TASKS_SUBSCRIBED.format(task=CHECKIN.title), texts.TASKS_UNSUBSCRIBED.format(task=CHECKIN.title),
              texts.PRAY_NO_BANNER, texts.PRAY_WRONG_CHAT, texts.PRAYSTATS_EMPTY, texts.RESET,
              texts.DELETE_DENIED, texts.DELETE_DONE, texts.DELETE_NOTHING, texts.PRIVATE_DENIED, texts.FOREIGN_CHAT]
    assert all(len(a) <= 200 for a in alerts)
