"""Меню Лизы — inline-кнопки вместо команд: молитва, рейтинг, задания, сброс диалога, удаление.

/start и /menu присылают меню; в группе меню от администратора закрепляется, прежнее удаляется.
Нажатия кнопок Telegram доставляет боту при любом режиме приватности и с id нажавшего.
Данные кнопок: menu:pray, menu:stats, menu:reset, menu:delete; задания — tasks:* (handlers/tasks.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import CallbackQuery, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.handlers.commands import delete_last
from bot.handlers.common import check_access
from bot.handlers.pray import PRAY_BUTTON, PRAY_MARKUP, STATS_BUTTON, pray_for, rating_text
from bot.handlers.tasks import MENU_BUTTON as TASKS_BUTTON
from bot.messaging import describe_user, is_admin, send_reply
from bot.services import Services, get_services

log = logging.getLogger(__name__)

RESET_BUTTON = InlineKeyboardButton("🧹 Забыть наш разговор", callback_data="menu:reset")
DELETE_BUTTON = InlineKeyboardButton("🗑 Стереть последний ответ Лизы", callback_data="menu:delete")
MENU_KIND = "menu"  # id закреплённого меню по чатам (в базе)


def main_menu(svc: Services, chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    if chat_id == svc.settings.pray_chat_id:
        rows += [[PRAY_BUTTON, STATS_BUTTON], [TASKS_BUTTON]]
    rows += [[RESET_BUTTON], [DELETE_BUTTON]]
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    svc = get_services(context)
    # Меню не попадает в учёт для удаления: «Стереть последний ответ» не должна стирать само меню
    await send_reply(update.message, texts.START.format(chunks=svc.collection.count()),
                     reply_markup=main_menu(svc, update.effective_chat.id),
                     message_thread_id=update.message.message_thread_id)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    svc = get_services(context)
    msg = update.message
    chat = update.effective_chat
    menu = await send_reply(msg, texts.MENU, reply_markup=main_menu(svc, chat.id),
                            message_thread_id=msg.message_thread_id, do_quote=False)
    if menu is None or chat.type == Chat.PRIVATE or not is_admin(update.effective_user, svc.settings.admin_usernames):
        return
    # Закрепить новое меню, прежнее убрать (нужны права администратора у бота)
    old = await asyncio.to_thread(svc.state.get, MENU_KIND, str(chat.id))
    if old and old != menu.message_id:
        with contextlib.suppress(TelegramError):
            await context.bot.delete_message(chat.id, old)
    try:
        await menu.pin(disable_notification=True)
    except TelegramError as e:
        log.warning("Не удалось закрепить меню в чате %s: %s", chat.id, e)
    await svc.state.save(MENU_KIND, str(chat.id), menu.message_id)
    with contextlib.suppress(TelegramError):
        await msg.delete()  # саму команду убираем


async def _allowed(svc: Services, query: CallbackQuery) -> bool:
    if svc.access.unrestricted:
        return True
    chat = query.message.chat
    if chat.type == Chat.PRIVATE:
        if svc.access.private_allowed(query.from_user.id):
            return True
        await query.answer(texts.PRIVATE_DENIED, show_alert=True)
        return False
    if svc.access.chat_allowed(chat.id):
        return True
    await query.answer(texts.FOREIGN_CHAT, show_alert=True)
    return False


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    query = update.callback_query
    user = query.from_user
    if not query.message or not await _allowed(svc, query):
        return
    chat_id = query.message.chat.id
    action = query.data.removeprefix("menu:")

    if action in ("pray", "stats") and chat_id != svc.settings.pray_chat_id:
        await query.answer(texts.PRAY_WRONG_CHAT, show_alert=True)
    elif action == "pray":
        ok, text = await pray_for(svc, user)
        if not ok:  # лимит или нет баннера — только нажавшему, без сообщений в чат
            await query.answer(text, show_alert=True)
            return
        await query.answer()
        await svc.reply(query.message, text, reply_markup=PRAY_MARKUP, do_quote=False)
    elif action == "stats":
        text = rating_text(svc, user.id)
        if text is None:
            await query.answer(texts.PRAYSTATS_EMPTY, show_alert=True)
            return
        await query.answer()
        await svc.reply(query.message, text, reply_markup=PRAY_MARKUP, do_quote=False)
    elif action == "reset":
        await svc.dialogs.reset((chat_id, user.id))  # только диалог нажавшего в этом чате
        log.info("Сброс диалога кнопкой: %s в чате %s", describe_user(user), chat_id)
        await query.answer(texts.RESET, show_alert=True)
    elif action == "delete":
        if not is_admin(user, svc.settings.admin_usernames):
            log.info("Удаление кнопкой отклонено: %s", describe_user(user))
            await query.answer(texts.DELETE_DENIED, show_alert=True)
            return
        deleted = await delete_last(svc, context.bot, chat_id)
        log.info("Удаление кнопкой от %s в чате %s: %s", describe_user(user), chat_id,
                 f"удалено {deleted}" if deleted else "нечего удалять")
        await query.answer(texts.DELETE_DONE if deleted else texts.DELETE_NOTHING)
    else:
        await query.answer()
