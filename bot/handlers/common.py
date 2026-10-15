"""Проверка доступа и служебные обработчики: ошибки, добавление бота в чужой чат."""

from __future__ import annotations

import logging

from telegram import Chat, Update
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from bot import texts
from bot.messaging import describe_user
from bot.services import get_services

log = logging.getLogger(__name__)


async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Разрешённый чат или личка пользователя из ALLOWED_USERS. Иначе — отказ без Claude."""
    svc = get_services(context)
    if svc.access.unrestricted:
        return True
    chat = update.effective_chat
    msg = update.message

    if chat.type == Chat.PRIVATE:
        if svc.access.private_allowed(update.effective_user.id):
            return True
        log.info("Отказ в личке: %s", describe_user(update.effective_user))
        await svc.reply(msg, texts.PRIVATE_DENIED)
        return False

    if svc.access.chat_allowed(chat.id):
        return True
    log.warning("Чужой чат %s (%s) — выхожу", chat.id, chat.title)
    await msg.reply_text(texts.FOREIGN_CHAT)
    await chat.leave()
    return False


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Бота добавили в группу — сразу выйти, если она не разрешена."""
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE or get_services(context).access.chat_allowed(chat.id):
        return
    if update.my_chat_member.new_chat_member.status in ("member", "administrator"):
        log.warning("Добавили в чужой чат %s (%s) — выхожу", chat.id, chat.title)
        await chat.leave()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Общий обработчик: сетевые сбои — коротко, остальное — с трейсбеком."""
    if isinstance(context.error, NetworkError):
        log.warning("Сетевая ошибка Telegram: %r", context.error)
    else:
        log.error("Необработанная ошибка", exc_info=context.error)
