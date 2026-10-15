"""Отправка ответов в Telegram и учёт отправленных сообщений (для /delete)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection

from telegram import Message, User
from telegram.error import BadRequest, NetworkError

from bot.config import MAX_TRACKED
from common.state import StateStore

log = logging.getLogger(__name__)

SEND_ATTEMPTS = 3


class SentMessages:
    """id сообщений бота по чатам.

    Telegram не отдаёт боту список его сообщений, поэтому id запоминаются сами
    и сохраняются в базу (записи вида «sent», по чату) — /delete работает и после
    перезапуска (если сообщению < 48 ч).
    """

    KIND = "sent"

    def __init__(self, store: StateStore, max_per_chat: int = MAX_TRACKED):
        self.store = store
        self.max_per_chat = max_per_chat
        self._ids: dict[str, list[int]] = store.load_all(self.KIND)

    async def track(self, message: Message) -> None:
        ids = self._ids.setdefault(str(message.chat_id), [])
        ids.append(message.message_id)
        del ids[:-self.max_per_chat]
        await self.save(message.chat_id)

    def pop(self, chat_id: int) -> int | None:
        """Забрать id последнего сообщения бота в чате (без сохранения — вызовите save)."""
        ids = self._ids.get(str(chat_id))
        return ids.pop() if ids else None

    async def save(self, chat_id: int) -> None:
        await self.store.save(self.KIND, str(chat_id), self._ids.get(str(chat_id), []))


async def send_reply(msg: Message, text: str, sent: SentMessages | None = None,
                     **kwargs) -> Message | None:
    """reply_text с повторами при сетевых сбоях (связь с Telegram идёт через VPN и иногда моргает)."""
    # Исходное сообщение могли удалить, пока Лиза думала, — тогда ответ уходит просто в чат
    kwargs.setdefault("allow_sending_without_reply", True)
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            reply = await msg.reply_text(text, **kwargs)
        except BadRequest:
            raise  # подкласс NetworkError, но повтор не поможет
        except NetworkError as e:
            if attempt == SEND_ATTEMPTS:
                raise
            delay = 2 * attempt
            log.warning("Telegram недоступен (%r), повтор отправки через %d с", e, delay)
            await asyncio.sleep(delay)
        else:
            if sent is not None and reply is not None:
                await sent.track(reply)
            return reply
    return None


def describe_user(user: User | None) -> str:
    """Для логов: «@username (Имя Фамилия)», без username — с id."""
    if user is None:
        return "неизвестный"
    name = user.full_name or ""
    if user.username:
        return f"@{user.username} ({name})" if name else f"@{user.username}"
    return f"{name} (id {user.id}, без username)"


def prompt_user(user: User | None) -> str:
    """Для Claude: «@username (Имя)» — по username промпт узнаёт знакомых; id в промпт не попадает."""
    if user is None:
        return "неизвестный"
    name = user.full_name or ""
    if user.username:
        return f"@{user.username} ({name})" if name else f"@{user.username}"
    return f"{name or 'без имени'} (без username)"


def is_admin(user: User | None, admin_usernames: Collection[str]) -> bool:
    return bool(user and user.username and user.username.lower() in admin_usernames)


def mention(user: User) -> str:
    return f"@{user.username}" if user.username else user.first_name


def is_addressed_to_bot(msg: Message, bot_username: str | None) -> bool:
    """Личка, упоминание @бота или ответ на сообщение бота."""
    if msg.chat.type == "private":
        return True
    if bot_username and msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                text = msg.text[entity.offset : entity.offset + entity.length]
                if text.lower() == f"@{bot_username}".lower():
                    return True
    reply = msg.reply_to_message
    return bool(reply and reply.from_user and reply.from_user.username == bot_username)
