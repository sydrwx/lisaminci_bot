"""Задания Лизы — только в личке: список с записью/отпиской, напоминание в 19:00 (UTC+3) и подтверждение.

Работают без Claude, для участников разрешённых чатов (как и вся личка). Данные кнопок:
  tasks:menu               — открыть свой список заданий (в группе — открывает личку с Лизой)
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

from telegram import Bot, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.messaging import describe_user, mention
from bot.pray import RESET_TZ, pray_day
from bot.services import Services, get_services
from bot.tasks import CHECKIN, TASKS, Done, Task, next_reminder, reminder_at

log = logging.getLogger(__name__)

MENU_BUTTON = InlineKeyboardButton("📋 Задания Лизы", callback_data="tasks:menu")
SEND_PAUSE = 0.05  # сек между личными напоминаниями (лимиты Telegram на рассылку)


def private_link(bot: Bot, start: str = "tasks") -> str:
    """Ссылка, открывающая личку с Лизой (с параметром для /start)."""
    return f"https://t.me/{bot.username}?start={start}"


def private_button(bot: Bot, text: str = "📚 В библиотеку к Лизе: задания и рейтинг") -> InlineKeyboardButton:
    return InlineKeyboardButton(text, url=private_link(bot))


def menu_markup(svc: Services, user_id: int) -> InlineKeyboardMarkup:
    rows = []
    for task in TASKS.values():
        mark = "✅" if svc.tasks.is_subscribed(user_id, task.id) else "➕"
        rows.append([InlineKeyboardButton(f"{mark} {task.title} (+{task.reward} молитва)",
                                          callback_data=f"tasks:sub:{task.id}:{user_id}")])
    rows.append([InlineKeyboardButton("✖ Закрыть", callback_data=f"tasks:close:{user_id}")])
    return InlineKeyboardMarkup(rows)


async def send_task_list(svc: Services, msg: Message, user: User) -> None:
    await svc.reply(msg, texts.TASKS_MENU.format(user=mention(user)), reply_markup=menu_markup(svc, user.id),
                    do_quote=False)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    msg = update.message
    if update.effective_chat.type != Chat.PRIVATE:
        await svc.reply(msg, texts.TASKS_PRIVATE_ONLY,
                        reply_markup=InlineKeyboardMarkup([[private_button(context.bot)]]))
        return
    if not await svc.access.private_allowed(context.bot, update.effective_user.id):
        await svc.reply(msg, texts.PRIVATE_DENIED)
        return
    await send_task_list(svc, msg, update.effective_user)


def reminder_text(task: Task, name: str) -> str:
    return random.choice(texts.TASK_CHECKIN_REMINDER).format(user=name or "милый")


def reminder_markup(task: Task, day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я отметился(-ась) у Паймон", callback_data=f"tasks:done:{task.id}:{day}")],
    ])


async def on_task_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = get_services(context)
    query = update.callback_query
    user = query.from_user
    if not query.message:
        await query.answer()
        return
    if query.message.chat.type != Chat.PRIVATE:
        # Старые кнопки в группе: сразу открыть личку с Лизой
        await query.answer(url=private_link(context.bot))
        return
    if not await svc.access.private_allowed(context.bot, user.id):
        await query.answer(texts.PRIVATE_DENIED, show_alert=True)
        return

    action, *args = query.data.removeprefix("tasks:").split(":")
    if action == "menu":
        await query.answer()
        await send_task_list(svc, query.message, user)
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
    left = svc.pray.prays_left(user.id)
    await query.answer(texts.TASK_REWARD.format(reward=task.reward, left=left), show_alert=True)
    with contextlib.suppress(BadRequest):  # кнопку убираем — второй раз нажимать нечего
        await query.edit_message_text(f"{query.message.text}\n\n" + texts.TASK_DONE_NOTE.format(left=left))


async def send_reminders(bot: Bot, svc: Services, task: Task, now: datetime) -> None:
    """Личное напоминание каждому записавшемуся, кто ещё не выполнил задание и состоит в чатах Лизы."""
    day = pray_day(now)
    sent = 0
    for uid, st in svc.tasks.subscribers(task.id):
        if svc.tasks.done_today(uid, task.id, now) or not await svc.access.private_allowed(bot, int(uid)):
            continue
        try:
            await bot.send_message(int(uid), reminder_text(task, st.get("name", "")),
                                   reply_markup=reminder_markup(task, day))
            sent += 1
        except Forbidden as e:  # заблокировал бота
            log.info("Напоминание «%s» для %s не доставлено: %s", task.title, uid, e)
        except TelegramError as e:
            log.warning("Напоминание «%s» для %s не отправлено: %r", task.title, uid, e)
        await asyncio.sleep(SEND_PAUSE)
    log.info("Напоминание «%s» за %s: отправлено %d", task.title, day, sent)
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
                await send_reminders(bot, svc, task, now)
        except Exception:
            log.exception("Напоминание «%s» не отправлено, повтор через минуту", task.title)
            await asyncio.sleep(60)
            continue
        await asyncio.sleep(max(1.0, (next_reminder(datetime.now(UTC)) - datetime.now(UTC)).total_seconds()))
