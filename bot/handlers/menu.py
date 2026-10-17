"""Меню Лизы — inline-кнопки вместо команд.

В группе — только «Помолиться» (в PRAY_CHAT_ID) и ссылка в личку; меню от администратора
закрепляется, прежнее удаляется. В личке — задания, рейтинг и сброс диалога.
/start и /menu присылают меню; /start tasks и /start stats (ссылки из группы) — сразу нужный раздел.
Нажатия кнопок Telegram доставляет боту при любом режиме приватности и с id нажавшего.
Данные кнопок: menu:pray, menu:stats, menu:reset, menu:delete (старые меню); задания — tasks:*.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import Bot, CallbackQuery, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.handlers.commands import delete_last
from bot.handlers.common import check_access
from bot.handlers.pray import PRAY_BUTTON, PRAY_MARKUP, STATS_BUTTON, pray_for, rating_text
from bot.handlers.tasks import MENU_BUTTON as TASKS_BUTTON
from bot.handlers.tasks import private_button, private_link, send_task_list
from bot.messaging import describe_user, is_admin, send_reply
from bot.services import Services, get_services

log = logging.getLogger(__name__)

RESET_BUTTON = InlineKeyboardButton("🧹 Забыть наш разговор", callback_data="menu:reset")
MENU_KIND = "menu"  # id закреплённого меню по чатам (в базе)


def group_menu(svc: Services, bot: Bot, chat_id: int) -> InlineKeyboardMarkup:
    rows = [[PRAY_BUTTON]] if chat_id == svc.settings.pray_chat_id else []
    rows.append([private_button(bot)])
    return InlineKeyboardMarkup(rows)


def private_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[TASKS_BUTTON], [STATS_BUTTON], [RESET_BUTTON]])


def chat_menu(svc: Services, bot: Bot, chat: Chat) -> InlineKeyboardMarkup:
    return private_menu() if chat.type == Chat.PRIVATE else group_menu(svc, bot, chat.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    svc = get_services(context)
    msg = update.message
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE and context.args:  # пришли по ссылке из группы
        if context.args[0] == "tasks":
            await send_task_list(svc, msg, update.effective_user)
            return
        if context.args[0] == "stats":
            await svc.reply(msg, rating_text(svc, update.effective_user.id) or texts.PRAYSTATS_EMPTY)
            return
    # Меню не попадает в учёт для /delete: удаление не должно стирать само меню
    await send_reply(msg, texts.START.format(chunks=svc.collection.count()),
                     reply_markup=chat_menu(svc, context.bot, chat), message_thread_id=msg.message_thread_id)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    svc = get_services(context)
    msg = update.message
    chat = update.effective_chat
    text = texts.MENU if chat.type == Chat.PRIVATE else texts.MENU_GROUP
    menu = await send_reply(msg, text, reply_markup=chat_menu(svc, context.bot, chat),
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


async def _allowed(svc: Services, bot: Bot, query: CallbackQuery) -> bool:
    if svc.access.unrestricted:
        return True
    chat = query.message.chat
    if chat.type == Chat.PRIVATE:
        if await svc.access.private_allowed(bot, query.from_user.id):
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
    if not query.message or not await _allowed(svc, context.bot, query):
        return
    chat = query.message.chat
    private = chat.type == Chat.PRIVATE
    action = query.data.removeprefix("menu:")

    if action == "pray":
        if chat.id != svc.settings.pray_chat_id:
            await query.answer(texts.PRAY_WRONG_CHAT, show_alert=True)
            return
        ok, text = await pray_for(svc, user)
        if not ok:  # лимит или нет баннера — только нажавшему, без сообщений в чат
            await query.answer(text, show_alert=True)
            return
        await query.answer()
        await svc.reply(query.message, text, reply_markup=PRAY_MARKUP, do_quote=False)
    elif action == "stats":
        if not private:  # кнопка из старого меню в группе — рейтинг теперь в личке
            await query.answer(url=private_link(context.bot, "stats"))
            return
        text = rating_text(svc, user.id)
        if text is None:
            await query.answer(texts.PRAYSTATS_EMPTY, show_alert=True)
            return
        await query.answer()
        await svc.reply(query.message, text, do_quote=False)
    elif action == "reset":
        await svc.dialogs.reset((chat.id, user.id))  # только диалог нажавшего в этом чате
        log.info("Сброс диалога кнопкой: %s в чате %s", describe_user(user), chat.id)
        await query.answer(texts.RESET, show_alert=True)
    elif action == "delete":  # кнопка из старого меню; в новых меню её нет — осталась /delete
        if not is_admin(user, svc.settings.admin_usernames):
            await query.answer(texts.DELETE_DENIED, show_alert=True)
            return
        deleted = await delete_last(svc, context.bot, chat.id)
        await query.answer(texts.DELETE_DONE if deleted else texts.DELETE_NOTHING)
    else:
        await query.answer()
