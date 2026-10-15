import asyncio
import random

from bot.pray import HARD_PITY, PrayStore, format_rating, new_user_state, roll_once
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
