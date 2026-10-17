"""/start, /reset, /delete."""

from __future__ import annotations

import contextlib
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.handlers.common import check_access
from bot.messaging import describe_user, is_admin
from bot.services import get_services

log = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    svc = get_services(context)
    await svc.reply(update.message, texts.START.format(chunks=svc.collection.count()))


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    svc = get_services(context)
    await svc.dialogs.reset((update.effective_chat.id, update.effective_user.id))  # только в текущем чате
    await svc.reply(update.message, texts.RESET)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удалить последнее сообщение Лизы в этом чате. Только для ADMIN_USERNAMES."""
    svc = get_services(context)
    msg = update.message
    user = update.effective_user
    chat_id = update.effective_chat.id
    if not is_admin(user, svc.settings.admin_usernames):
        log.info("/delete отклонён: %s", describe_user(user))
        await svc.reply(msg, texts.DELETE_DENIED)
        return

    deleted = None
    while deleted is None and (message_id := svc.sent.pop(chat_id)) is not None:
        try:
            await context.bot.delete_message(chat_id, message_id)
            deleted = message_id
        except TelegramError as e:
            # уже удалено вручную или старше 48 ч — пробуем предыдущее
            log.warning("/delete: сообщение %s не удалено: %s", message_id, e)
    await svc.sent.save(chat_id)

    # Саму команду тоже убираем (в группе — если у бота есть право удалять сообщения)
    with contextlib.suppress(TelegramError):
        await msg.delete()

    if deleted is None:
        await context.bot.send_message(chat_id, texts.DELETE_NOTHING, message_thread_id=msg.message_thread_id)
    log.info("/delete от %s в чате %s: %s", describe_user(user), chat_id,
             f"удалено {deleted}" if deleted else "нечего удалять")
