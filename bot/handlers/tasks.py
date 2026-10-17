"""Задания Лизы: кнопка «Задания Лизы» (и старая /tasks), кнопки записи и подтверждения, напоминание в 19:00 (UTC+3).

Работают только в PRAY_CHAT_ID и без Claude. Данные кнопок:
  tasks:menu               — открыть свой список заданий
  tasks:sub:<task>:<uid>   — записаться/отписаться (только владелец списка uid)
  tasks:close:<uid>        — убрать свой список
  tasks:done:<task>:<day>  — подтвердить задание за сутки day (награда — нажавшему)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import UTC, datetime

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.messaging import describe_user, mention
from bot.pray import RESET_TZ, pray_day
from bot.services import Services, get_services
from bot.tasks import CHECKIN, TASKS, Done, Task, next_reminder, reminder_at, user_link

log = logging.getLogger(__name__)

MENU_BUTTON = InlineKeyboardButton("📋 Задания Лизы", callback_data="tasks:menu")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    msg = update.message
    if update.effective_chat.id != svc.settings.pray_chat_id:
        await svc.reply(msg, texts.TASKS_WRONG_CHAT)
        return
    await svc.reply(msg, texts.TASKS_INTRO, reply_markup=InlineKeyboardMarkup([[MENU_BUTTON]]))


def menu_markup(svc: Services, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for task in TASKS.values():
        mark = "✅" if svc.tasks.is_subscribed(user_id, task.id) else "➕"
        rows.append([InlineKeyboardButton(f"{mark} {task.title} (+{task.reward} молитва)",
                                          callback_data=f"tasks:sub:{task.id}:{user_id}")])
    rows.append([InlineKeyboardButton("✖ Закрыть", callback_data=f"tasks:close:{user_id}")])
    return InlineKeyboardMarkup(rows)


def reminder_text(svc: Services, task: Task, now: datetime) -> str:
    subs = svc.tasks.subscribers(task.id)
    tags = ", ".join(user_link(uid, st) for uid, st in subs)
    done = [user_link(uid, st) for uid, st in subs if svc.tasks.done_today(uid, task.id, now)]
    text = random.Random(pray_day(now)).choice(texts.TASK_CHECKIN_REMINDER).format(users=tags)
    if done:
        text += "\n\n✅ Отметились: " + ", ".join(done)
    return text


def reminder_markup(task: Task, day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я отметился(-ась) у Паймон", callback_data=f"tasks:done:{task.id}:{day}")],
        [MENU_BUTTON],
    ])


async def on_task_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    query = update.callback_query
    user = query.from_user
    if not query.message or query.message.chat.id != svc.settings.pray_chat_id:
        await query.answer(texts.TASKS_WRONG_CHAT, show_alert=True)
        return

    action, *args = query.data.removeprefix("tasks:").split(":")
    if action == "menu":
        await query.answer()
        await svc.reply(query.message, texts.TASKS_MENU.format(user=mention(user)),
                        reply_markup=menu_markup(svc, user.id))
    elif action in ("sub", "close"):
        owner = int(args[-1])
        if owner != user.id:
            await query.answer(texts.TASKS_NOT_YOURS, show_alert=True)
            return
        if action == "close":
            await query.answer()
            with contextlib.suppress(TelegramError):
                await query.message.delete()
            return
        task = TASKS.get(args[0])
        if task is None:
            await query.answer(texts.TASKS_UNKNOWN, show_alert=True)
            return
        subscribed = svc.tasks.toggle(user.id, user.full_name, user.username or "", task.id)
        await svc.tasks.save_user(user.id)
        log.info("Задания: %s %s «%s»", describe_user(user), "записан на" if subscribed else "отписан от",
                 task.title)
        await query.answer((texts.TASKS_SUBSCRIBED if subscribed else texts.TASKS_UNSUBSCRIBED)
                           .format(task=task.title))
        with contextlib.suppress(BadRequest):  # «message is not modified»
            await query.edit_message_reply_markup(menu_markup(svc, user.id))
    elif action == "done":
        await _confirm(svc, query, user, TASKS.get(args[0]), args[1])
    else:
        await query.answer()


async def _confirm(svc: Services, query, user: User, task: Task | None, day: str) -> None:
    if task is None:
        await query.answer(texts.TASKS_UNKNOWN, show_alert=True)
        return
    now = datetime.now(UTC)
    result = svc.tasks.complete(user.id, task.id, day, now)
    log.info("Задание «%s» от %s за %s: %s", task.title, describe_user(user), day, result.value)
    if result is not Done.OK:
        await query.answer({Done.NOT_SUBSCRIBED: texts.TASK_NOT_SUBSCRIBED,
                            Done.EXPIRED: texts.TASK_EXPIRED,
                            Done.ALREADY: random.choice(texts.TASK_ALREADY)}[result], show_alert=True)
        return
    await svc.tasks.save_user(user.id)
    await svc.pray.save_user(user.id)
    await query.answer(texts.TASK_REWARD.format(reward=task.reward, left=svc.pray.prays_left(user.id)),
                       show_alert=True)
    with contextlib.suppress(BadRequest):  # «message is not modified»
        await query.edit_message_text(reminder_text(svc, task, now), parse_mode=ParseMode.HTML,
                                      reply_markup=reminder_markup(task, day))


async def send_reminder(bot: Bot, svc: Services, task: Task, now: datetime) -> None:
    day = pray_day(now)
    if svc.tasks.subscribers(task.id):
        msg = await bot.send_message(svc.settings.pray_chat_id, reminder_text(svc, task, now),
                                     parse_mode=ParseMode.HTML, reply_markup=reminder_markup(task, day))
        await svc.sent.track(msg)
        log.info("Напоминание «%s» за %s: %d участников", task.title, day, len(svc.tasks.subscribers(task.id)))
    await svc.tasks.set_reminded(task.id, day)


async def remind_daily(bot: Bot, svc: Services, task: Task = CHECKIN) -> None:
    """Фоновая задача: напоминание в REMINDER_TIME. Если бот стартовал позже и сегодня
    напоминания ещё не было — отправляет сразу (отметка о сутках хранится в базе)."""
    if not svc.settings.pray_chat_id:
        return
    while True:
        now = datetime.now(UTC)
        due = reminder_at(now.astimezone(RESET_TZ).date())
        try:
            if now >= due and await asyncio.to_thread(svc.tasks.reminded_day, task.id) != pray_day(now):
                await send_reminder(bot, svc, task, now)
        except Exception:
            log.exception("Напоминание «%s» не отправлено, повтор через минуту", task.title)
            await asyncio.sleep(60)
            continue
        await asyncio.sleep(max(1.0, (next_reminder(datetime.now(UTC)) - datetime.now(UTC)).total_seconds()))
