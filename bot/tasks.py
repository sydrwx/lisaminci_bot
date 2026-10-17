"""Задания Лизы: запись на задания, ежедневное напоминание и награда молитвами /pray.

Подтверждение — кнопкой под напоминанием: кто нажал, тот и получает награду (по Telegram id),
за каждые сутки (UTC+3) — один раз. Кнопки вчерашних напоминаний не работают.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum

from bot.pray import RESET_TZ, PrayStore, pray_day
from common.state import StateStore

REMINDER_TIME = time(19, 0)  # по RESET_TZ (UTC+3)


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    reward: int  # бонусных молитв за выполнение


CHECKIN = Task("checkin", "Ежедневная отметка у Паймон", reward=1)
TASKS = {t.id: t for t in (CHECKIN,)}


class Done(Enum):
    OK = "ok"
    NOT_SUBSCRIBED = "not_subscribed"
    EXPIRED = "expired"      # кнопка напоминания за прошлые сутки
    ALREADY = "already"      # уже подтверждено сегодня


def reminder_at(day: date) -> datetime:
    return datetime.combine(day, REMINDER_TIME, RESET_TZ)


def next_reminder(now: datetime) -> datetime:
    """Ближайшее напоминание не раньше now."""
    today = reminder_at(now.astimezone(RESET_TZ).date())
    return today if now <= today else today + timedelta(days=1)


def user_link(uid: str, st: dict) -> str:
    """HTML-упоминание по id: тегает и пользователей без username."""
    name = st.get("name") or (f"@{st['username']}" if st.get("username") else uid)
    return f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'


class TaskStore:
    """Кто на какие задания записан и когда их выполнял (записи вида «tasks» в базе)."""

    KIND = "tasks"
    META = "tasks_meta"  # служебное: за какие сутки уже отправлено напоминание

    def __init__(self, store: StateStore, pray: PrayStore):
        self.store = store
        self.pray = pray
        self.state: dict[str, dict] = store.load_all(self.KIND)

    def user(self, user_id: int) -> dict:
        return self.state.get(str(user_id), {})

    def is_subscribed(self, user_id: int, task_id: str) -> bool:
        return task_id in self.user(user_id).get("tasks", [])

    def toggle(self, user_id: int, name: str, username: str, task_id: str) -> bool:
        """Записать на задание или отписать; вернёт, записан ли теперь (сохранить — save_user)."""
        st = self.state.setdefault(str(user_id), {"tasks": [], "done": {}})
        st.update(name=name, username=username)
        tasks = st.setdefault("tasks", [])
        if task_id in tasks:
            tasks.remove(task_id)
            return False
        tasks.append(task_id)
        return True

    def subscribers(self, task_id: str) -> list[tuple[str, dict]]:
        return [(uid, st) for uid, st in self.state.items() if task_id in st.get("tasks", [])]

    def done_today(self, user_id: int | str, task_id: str, now: datetime) -> bool:
        return self.state.get(str(user_id), {}).get("done", {}).get(task_id) == pray_day(now)

    def all_done_today(self, user_id: int, now: datetime | None = None) -> bool:
        """Записан хотя бы на одно задание, и все они на сегодня выполнены."""
        now = now or datetime.now(UTC)
        tasks = [t for t in self.user(user_id).get("tasks", []) if t in TASKS]
        return bool(tasks) and all(self.done_today(user_id, t, now) for t in tasks)

    def complete(self, user_id: int, task_id: str, day: str, now: datetime) -> Done:
        """Подтверждение задания за сутки day; при успехе начисляет молитвы.

        Проверка и начисление — без await, поэтому двойное нажатие не даст двух наград.
        Сохранить — save_user и pray.save_user.
        """
        if not self.is_subscribed(user_id, task_id):
            return Done.NOT_SUBSCRIBED
        if day != pray_day(now):
            return Done.EXPIRED
        if self.done_today(user_id, task_id, now):
            return Done.ALREADY
        self.state[str(user_id)].setdefault("done", {})[task_id] = day
        self.pray.grant(user_id, TASKS[task_id].reward)
        return Done.OK

    async def save_user(self, user_id: int) -> None:
        await self.store.save(self.KIND, str(user_id), self.state[str(user_id)])

    def reminded_day(self, task_id: str) -> str | None:
        return self.store.get(self.META, task_id)

    async def set_reminded(self, task_id: str, day: str) -> None:
        await self.store.save(self.META, task_id, day)
