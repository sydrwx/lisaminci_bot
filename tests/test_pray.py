import asyncio
import random
from datetime import UTC, datetime, timedelta

from bot.pray import HARD_PITY, PrayStore, format_rating, format_wait, new_user_state, roll_once, until_reset
from common.state import StateStore
from tests.fakes import FakeCollection

BANNER = {"name": "Тест", "featured": ["Фурина"], "featured_rare": ["Беннет"]}


class FixedRng:
    """random() всегда value, choice — первый элемент."""

    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0]


def test_hard_pity_gives_five_star():
    st = new_user_state() | {"pity5": HARD_PITY - 1}
    wish = roll_once(st, BANNER, bonus=False, rng=FixedRng(0.99))
    assert wish.rarity == "5lost" and wish.n == HARD_PITY
    assert st["pity5"] == 0 and st["guaranteed"]
    # после проигрыша 50/50 следующая 5★ — с баннера
    st["pity5"] = HARD_PITY - 1
    assert roll_once(st, BANNER, bonus=False, rng=FixedRng(0.99)).item == "Фурина"
    assert not st["guaranteed"]


def test_four_star_every_ten():
    st = new_user_state() | {"pity4": 9}
    wish = roll_once(st, BANNER, bonus=False, rng=FixedRng(0.99))
    assert (wish.rarity, wish.item) == ("4", "Беннет")
    assert st["pity4"] == 0


def test_jean_bonus_keeps_pity():
    st = new_user_state()
    wish = roll_once(st, BANNER, bonus=True, rng=FixedRng(0.0))
    assert wish.rarity == "jean" and st["pity5"] == 1


def test_never_exceeds_hard_pity():
    st = new_user_state()
    rng = random.Random(1)
    for _ in range(2000):
        roll_once(st, BANNER, bonus=False, rng=rng)
        assert st["pity5"] < HARD_PITY


def test_store_roll_persists_and_migrates():
    db = StateStore(FakeCollection())
    db.put("pray", "1", {"pity5": 5, "pity4": 2, "guaranteed": False})
    store = PrayStore(db)
    assert store.user(1)["total"] == 5 and store.user(1)["stats"] == {"3": 5}
    store.roll(2, "lisa", BANNER)
    asyncio.run(store.save_user(2))
    assert PrayStore(db).user(2)["total"] == 1


def test_rating_order():
    state = {
        "1": {"username": "a", "total": 100, "stats": {"5": 1}, "pity5": 10},
        "2": {"username": "b", "total": 50, "stats": {"5": 1}, "pity5": 0},
        "3": {"username": "c", "total": 10, "stats": {"3": 10}, "pity5": 10},
    }
    text = format_rating(state, user_id=3)
    lines = text.splitlines()
    assert lines[1].startswith("🥇 @b") and lines[2].startswith("🥈 @a")
    assert "Ты на 3-м месте" in text and "Всего молитв в библиотеке: 160" in text


def test_daily_limit_resets_at_midnight_utc3():
    store = PrayStore(StateStore(FakeCollection()))
    evening = datetime(2026, 10, 17, 20, 30, tzinfo=UTC)  # 23:30 по UTC+3
    assert store.roll(1, "a", BANNER, now=evening) is not None
    assert store.prays_left(1, evening) == 0
    assert store.roll(1, "a", BANNER, now=evening + timedelta(minutes=29)) is None
    assert store.user(1)["total"] == 1
    # 00:00 по UTC+3 — новые сутки, хотя по UTC ещё 17-е
    midnight = datetime(2026, 10, 17, 21, 0, tzinfo=UTC)
    assert store.prays_left(1, midnight) == 1
    assert store.roll(1, "a", BANNER, now=midnight) is not None
    assert store.roll(1, "a", BANNER, now=midnight) is None


def test_extra_prays_after_daily_limit_and_persist():
    db = StateStore(FakeCollection())
    store = PrayStore(db, daily_limit=1)
    now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    assert store.grant(1, 2) == 2
    assert store.prays_left(1, now) == 3
    store.roll(1, "a", BANNER, now=now)
    assert store.user(1)["extra"] == 2  # сначала тратится суточный лимит
    store.roll(1, "a", BANNER, now=now)
    asyncio.run(store.save_user(1))
    # бонусные не сгорают в полночь и переживают перезапуск
    store = PrayStore(db, daily_limit=1)
    tomorrow = now + timedelta(days=1)
    assert store.prays_left(1, tomorrow) == 2
    assert store.prays_left(1, now) == 1


def test_until_reset():
    assert until_reset(datetime(2026, 10, 17, 20, 30, tzinfo=UTC)) == timedelta(minutes=30)
    assert until_reset(datetime(2026, 10, 17, 21, 0, tzinfo=UTC)) == timedelta(days=1)
    assert format_wait(timedelta(hours=5, minutes=12)) == "5 ч 12 мин"
    assert format_wait(timedelta(seconds=30)) == "1 мин"
