"""/pray и /praystats — работают только в PRAY_CHAT_ID и без Claude."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot import texts
from bot.messaging import describe_user, mention
from bot.pray import format_rating, format_wait, until_reset
from bot.services import get_services

log = logging.getLogger(__name__)


async def cmd_pray(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    msg = update.message
    if update.effective_chat.id != svc.settings.pray_chat_id:
        await svc.reply(msg, texts.PRAY_WRONG_CHAT)
        return

    user = update.effective_user
    if not svc.pray.prays_left(user.id):
        await _reply_limit(svc, msg, user)
        return

    banner = await asyncio.to_thread(svc.banners.current_character_banner)
    if not banner:
        await svc.reply(msg, texts.PRAY_NO_BANNER)
        return

    wish = svc.pray.roll(user.id, user.username or "", banner)
    if wish is None:  # параллельная /pray того же пользователя успела потратить молитву
        await _reply_limit(svc, msg, user)
        return
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
    log.info("/pray %s: %s %s (молитва %d, всего %d)", describe_user(user), wish.rarity, wish.item,
             wish.n, total)
    await svc.reply(msg, text)


async def _reply_limit(svc, msg, user) -> None:
    wait = format_wait(until_reset(datetime.now(UTC)))
    log.info("/pray %s: лимит на сегодня исчерпан", describe_user(user))
    await svc.reply(msg, random.choice(texts.PRAY_LIMIT).format(user=mention(user), wait=wait))


async def cmd_praystats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    msg = update.message
    if update.effective_chat.id != svc.settings.pray_chat_id:
        await svc.reply(msg, texts.PRAYSTATS_WRONG_CHAT)
        return
    if not svc.pray.state:
        await svc.reply(msg, texts.PRAYSTATS_EMPTY)
        return
    await svc.reply(msg, format_rating(svc.pray.state, update.effective_user.id))
