"""/pray — молитва на текущем баннере персонажа: шансы, счётчики гарантии, рейтинг.

Шансы как в игре: 5★ 0,6%, мягкая гарантия с 74-й (+6% за молитву), жёсткая — 90-я;
4★ 5,1%, гарантия каждые 10; 50/50 на 5★ персонажа баннера, после проигрыша — гарантия.

Лимит: daily_limit молитв в сутки, сброс в 00:00 по UTC+3. Сверх лимита — бонусные молитвы
(extra), которые выдаёт grant (например, за выполненные задания Лизы); они не сгорают в полночь.
"""

from __future__ import annotations

import random
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from common.state import StateStore

HARD_PITY = 90
SOFT_PITY = 74
BASE_5 = 0.006
SOFT_STEP = 0.06
BASE_4 = 0.051
JEAN_CHANCE = 0.10  # особая милость PRAY_BONUS_USERS: Джинн, даже если её нет в баннере

DAILY_LIMIT = 1                                  # молитв в сутки без бонусов
RESET_TZ = timezone(timedelta(hours=3), "UTC+3")  # сутки молитв начинаются в 00:00 по этому поясу

STANDARD_5 = ["Дилюк", "Джинн", "Ци Ци", "Мона", "Кэ Цин", "Тигнари", "Дэхья", "Мидзуки"]
WISH_3 = [
    "Холодное лезвие", "Предвестник зари", "Меч небесного всадника", "Металлическая тень",
    "Меч драконьей крови", "Дубина переговоров", "Большой меч небесного всадника", "Чёрная кисть",
    "Руководство по магии", "Эпос о драконоборцах", "Изумрудный шар", "Лук ворона",
    "Клятва стрелка", "Рогатка",
]


@dataclass(frozen=True)
class Wish:
    rarity: str  # "jean", "5", "5lost", "4", "3"
    item: str
    n: int       # номер молитвы с последней 5★
    left: int    # молитв до гарантии 5★


def new_user_state() -> dict:
    return {"pity5": 0, "pity4": 0, "guaranteed": False, "total": 0, "stats": {}}


def pray_day(now: datetime) -> str:
    """Сутки молитв (дата по RESET_TZ), к которым относится момент now."""
    return now.astimezone(RESET_TZ).date().isoformat()


def until_reset(now: datetime) -> timedelta:
    """Сколько осталось до следующего сброса лимита (00:00 по RESET_TZ)."""
    local = now.astimezone(RESET_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), RESET_TZ)
    return midnight - local


def format_wait(delta: timedelta) -> str:
    minutes = max(1, -(-int(delta.total_seconds()) // 60))  # округление вверх
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def roll_once(st: dict, banner: dict, bonus: bool, rng: random.Random | None = None) -> Wish:
    """Одна молитва; меняет счётчики гарантии в st."""
    rng = rng or random
    st["pity5"] += 1
    st["pity4"] += 1
    n = st["pity5"]

    # Бонус поверх обычной молитвы: счётчики гарантии не сбрасываются
    if bonus and rng.random() < JEAN_CHANCE:
        return Wish("jean", "Джинн", n, HARD_PITY - n)

    p5 = BASE_5 if n < SOFT_PITY else BASE_5 + SOFT_STEP * (n - SOFT_PITY + 1)
    if n >= HARD_PITY or rng.random() < p5:
        st.update(pity5=0, pity4=0)
        if st["guaranteed"] or rng.random() < 0.5:
            st["guaranteed"] = False
            return Wish("5", rng.choice(banner["featured"]), n, HARD_PITY)
        st["guaranteed"] = True
        lost = [c for c in STANDARD_5 if c not in banner["featured"]]
        return Wish("5lost", rng.choice(lost), n, HARD_PITY)

    left = HARD_PITY - st["pity5"]
    p4 = BASE_4 if st["pity4"] < 9 else 0.561
    if st["pity4"] >= 10 or rng.random() < p4:
        st["pity4"] = 0
        return Wish("4", rng.choice(banner.get("featured_rare") or WISH_3), n, left)
    return Wish("3", rng.choice(WISH_3), n, left)


class PrayStore:
    """Счётчики и статистика молитв по пользователям (записи вида «pray» в базе)."""

    KIND = "pray"

    def __init__(self, store: StateStore, bonus_users: Collection[str] = (), daily_limit: int = DAILY_LIMIT):
        self.store = store
        self.bonus_users = bonus_users
        self.daily_limit = daily_limit
        self.state: dict[str, dict] = store.load_all(self.KIND)
        # Записи до появления статистики: 5★ ещё не было, значит всего молитв = счётчик гарантии;
        # по логам до статистики выпадали 3★
        for st in self.state.values():
            st.setdefault("total", st.get("pity5", 0))
            st.setdefault("stats", {"3": st["total"]} if st["total"] else {})

    def user(self, user_id: int) -> dict | None:
        return self.state.get(str(user_id))

    def _used_today(self, st: dict, now: datetime) -> int:
        return st.get("today", 0) if st.get("day") == pray_day(now) else 0

    def prays_left(self, user_id: int, now: datetime | None = None) -> int:
        """Сколько молитв доступно сейчас: остаток суточного лимита плюс бонусные."""
        st = self.user(user_id) or {}
        now = now or datetime.now(UTC)
        return max(0, self.daily_limit - self._used_today(st, now)) + st.get("extra", 0)

    def grant(self, user_id: int, count: int = 1) -> int:
        """Выдать бонусные молитвы сверх суточного лимита (награда за задание); вернёт их запас.

        Сохранить — save_user.
        """
        st = self.state.setdefault(str(user_id), new_user_state())
        st["extra"] = st.get("extra", 0) + count
        return st["extra"]

    def roll(self, user_id: int, username: str, banner: dict, now: datetime | None = None) -> Wish | None:
        """Молитва пользователя с обновлением статистики (сохранить — save_user).

        None — молитвы на сегодня закончились. Сначала тратится суточный лимит, затем бонусные.
        """
        now = now or datetime.now(UTC)
        if not self.prays_left(user_id, now):
            return None
        st = self.state.setdefault(str(user_id), new_user_state())
        used = self._used_today(st, now)
        if used < self.daily_limit:
            st.update(day=pray_day(now), today=used + 1)
        else:
            st["extra"] -= 1
        stamp = now.isoformat(timespec="seconds")
        st["username"] = username
        st.setdefault("first", stamp)
        st["last"] = stamp
        st["total"] = st.get("total", 0) + 1
        wish = roll_once(st, banner, bonus=username.lower() in self.bonus_users)
        stats = st.setdefault("stats", {})
        stats[wish.rarity] = stats.get(wish.rarity, 0) + 1
        return wish

    async def save_user(self, user_id: int) -> None:
        await self.store.save(self.KIND, str(user_id), self.state[str(user_id)])


def format_rating(state: dict[str, dict], user_id: int, limit: int = 15) -> str:
    """Рейтинг чата: больше 5★ — выше; при равенстве — кому они дешевле дались."""

    def row(uid: str, st: dict) -> dict:
        stats = st.get("stats", {})
        five = stats.get("5", 0) + stats.get("5lost", 0)
        total = st.get("total", 0)
        return {"uid": uid, "name": st.get("username") or uid, "total": total, "five": five,
                "won": stats.get("5", 0), "jean": stats.get("jean", 0), "four": stats.get("4", 0),
                "per5": total / five if five else None}

    rows = sorted((row(u, st) for u, st in state.items()),
                  key=lambda r: (-r["five"], r["per5"] or 10**9, -r["jean"], -r["total"]))
    medals = ["🥇", "🥈", "🥉"]
    lines = ["📜 Рейтинг молитв библиотеки"]
    for i, r in enumerate(rows[:limit]):
        place = medals[i] if i < len(medals) else f"{i + 1}."
        parts = [f"5★: {r['five']}" + (f" (с баннера {r['won']})" if r["five"] else ""),
                 f"4★: {r['four']}", f"молитв: {r['total']}"]
        if r["jean"]:
            parts.append(f"Джинн: {r['jean']}")
        if r["per5"]:
            parts.append(f"≈{r['per5']:.0f} молитв на 5★")
        lines.append(f"{place} @{r['name']} — " + ", ".join(parts))

    me = next((r for r in rows if r["uid"] == str(user_id)), None)
    if me:
        st = state[me["uid"]]
        lines.append(f"\nТы на {rows.index(me) + 1}-м месте, милый. "
                     f"До гарантии 5★: {HARD_PITY - st.get('pity5', 0)}"
                     + (", следующая 5★ — с баннера" if st.get("guaranteed") else ""))
    lines.append(f"Всего молитв в библиотеке: {sum(r['total'] for r in rows)}")
    return "\n".join(lines)
