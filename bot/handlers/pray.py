"""Молитвы и рейтинг: кнопки меню (см. handlers/menu.py) и старые /pray, /praystats.

Молитва — только в PRAY_CHAT_ID, рейтинг — в личке (а /praystats от администратора — для всего
чата молитв); без Claude.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import UTC, datetime

from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.messaging import describe_user, is_admin, mention
from bot.pray import format_rating, format_wait, has_prayers, until_reset
from bot.services import Services, get_services

log = logging.getLogger(__name__)

PRAY_BUTTON = InlineKeyboardButton("🙏 Помолиться", callback_data="menu:pray")
STATS_BUTTON = InlineKeyboardButton("📜 Рейтинг молитв", callback_data="menu:stats")
PRAY_MARKUP = InlineKeyboardMarkup([[PRAY_BUTTON]])  # под результатом молитвы в группе


def limit_text(svc: Services, user: User) -> str:
    wait = format_wait(until_reset(datetime.now(UTC)))
    log.info("Молитва %s: лимит на сегодня исчерпан", describe_user(user))
    if svc.tasks.all_done_today(user.id):
        variants = texts.PRAY_LIMIT_TASKS_DONE
    elif svc.tasks.user(user.id).get("tasks"):
        variants = texts.PRAY_LIMIT_TASK_PENDING
    else:
        variants = texts.PRAY_LIMIT_INVITE
    return random.choice(variants).format(user=mention(user), wait=wait)


async def pray_for(svc: Services, user: User) -> tuple[bool, str]:
    """Молитва пользователя: (состоялась ли, текст). Не состоялась — текст для ответа только ему."""
    if not svc.pray.prays_left(user.id):
        return False, limit_text(svc, user)

    banner = await asyncio.to_thread(svc.banners.current_character_banner)
    if not banner:
        return False, texts.PRAY_NO_BANNER

    wish = svc.pray.roll(user.id, user.username or "", banner)
    if wish is None:  # параллельная молитва того же пользователя успела потратить последнюю
        return False, limit_text(svc, user)
    await svc.pray.save_user(user.id)
    total = svc.pray.user(user.id)["total"]
    text = random.choice(texts.PRAY_TEMPLATES[wish.rarity]).format(
        user=mention(user), item=wish.item, n=wish.n, left=wish.left)
    text += f"\nВсего молитв: {total}"
    left = svc.pray.prays_left(user.id)
    text += (f"\nОсталось молитв сегодня: {left}" if left
             else f"\nСледующая молитва — после полуночи (UTC+3), через {format_wait(until_reset(datetime.now(UTC)))}")
    if wish.rarity in ("4", "3"):
        text = f"«{banner['name']}»\n{text}"
    log.info("Молитва %s: %s %s (молитва %d, всего %d)", describe_user(user), wish.rarity, wish.item,
             wish.n, total)
    return True, text


PUBLIC = 0  # user_id для рейтинга без личной строки


def rating_text(svc: Services, user_id: int) -> str | None:
    return format_rating(svc.pray.state, user_id) if has_prayers(svc.pray.state) else None


async def cmd_pray(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    msg = update.message
    if update.effective_chat.id != svc.settings.pray_chat_id:
        await svc.reply(msg, texts.PRAY_WRONG_CHAT)
        return
    ok, text = await pray_for(svc, update.effective_user)
    await svc.reply(msg, text, reply_markup=PRAY_MARKUP if ok else None)


async def cmd_praystats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    if chat.type != Chat.PRIVATE and is_admin(user, svc.settings.admin_usernames):
        # Админ публикует рейтинг для всего чата — без строки «ты на N-м месте»
        if chat.id != svc.settings.pray_chat_id:
            await svc.reply(msg, texts.PRAYSTATS_WRONG_CHAT)
            return
        log.info("/praystats в чате от %s", describe_user(user))
        await svc.reply(msg, rating_text(svc, PUBLIC) or texts.PRAYSTATS_EMPTY, do_quote=False)
        with contextlib.suppress(TelegramError):
            await msg.delete()  # саму команду убираем
        return
    if chat.type != Chat.PRIVATE:  # остальным рейтинг — только в личке
        await svc.reply(msg, texts.PRAYSTATS_PRIVATE_ONLY, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📚 В библиотеку к Лизе", url=f"https://t.me/{context.bot.username}?start=stats")]]))
        return
    if not await svc.access.private_allowed(context.bot, user.id):
        await svc.reply(msg, texts.PRIVATE_DENIED)
        return
    await svc.reply(msg, rating_text(svc, user.id) or texts.PRAYSTATS_EMPTY)
