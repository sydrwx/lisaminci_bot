"""Доступ: Лиза отвечает в разрешённых чатах, а в личке — участникам этих чатов."""

from __future__ import annotations

import logging
import time
from collections.abc import Collection

from telegram import Bot

from bot.config import MEMBER_CACHE_TTL, NOT_MEMBER_CACHE_TTL

log = logging.getLogger(__name__)

MEMBER_STATUSES = ("creator", "administrator", "member")


class Access:
    def __init__(self, allowed_chats: Collection[int], cache_ttl: float = MEMBER_CACHE_TTL,
                 negative_ttl: float = NOT_MEMBER_CACHE_TTL):
        self.allowed_chats = allowed_chats
        self.cache_ttl = cache_ttl
        self.negative_ttl = negative_ttl
        self._members: dict[int, tuple[bool, float]] = {}

    @property
    def unrestricted(self) -> bool:
        return not self.allowed_chats

    def chat_allowed(self, chat_id: int) -> bool:
        return self.unrestricted or chat_id in self.allowed_chats

    async def private_allowed(self, bot: Bot, user_id: int) -> bool:
        """Можно ли общаться в личке: пользователь состоит хотя бы в одном разрешённом чате."""
        return self.unrestricted or await self.is_member(bot, user_id)

    async def is_member(self, bot: Bot, user_id: int) -> bool:
        """Состоит ли пользователь хотя бы в одном разрешённом чате.

        Ответ кэшируется: «да» — на cache_ttl, «нет» — на negative_ttl (только что вступившего
        не заставляем ждать долго).
        """
        cached = self._members.get(user_id)
        if cached:
            ok, at = cached
            if time.monotonic() - at < (self.cache_ttl if ok else self.negative_ttl):
                return ok
        ok = False
        for chat_id in self.allowed_chats:
            try:
                member = await bot.get_chat_member(chat_id, user_id)
            except Exception as e:
                log.warning("get_chat_member(%s, %s): %s", chat_id, user_id, e)
                continue
            if member.status in MEMBER_STATUSES or \
               (member.status == "restricted" and getattr(member, "is_member", False)):
                ok = True
                break
        self._members[user_id] = (ok, time.monotonic())
        return ok
