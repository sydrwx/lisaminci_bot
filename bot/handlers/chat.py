"""Вопрос Лизе: упоминание, ответ на её сообщение или любое сообщение в личке."""

from __future__ import annotations

import asyncio
import logging
import random
import re

import anthropic
from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import texts
from bot.config import MAX_TOKENS
from bot.game_info import current_game_info, today_info
from bot.handlers.common import check_access
from bot.messaging import describe_user, is_addressed_to_bot
from bot.prompt import build_messages
from bot.services import Services, get_services
from bot.topics import Topic, detect_topic

log = logging.getLogger(__name__)


def strip_bot_mention(text: str, bot_username: str | None) -> str:
    if bot_username:
        text = re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE)
    return text.strip()


def question_header(msg: Message, bot_id: int) -> str:
    """Кто пишет и, если это ответ на чужое сообщение (не бота), — о ком и о чём речь."""
    header = f"Пишет: {describe_user(msg.from_user)}"
    reply = msg.reply_to_message
    if reply and reply.from_user and reply.from_user.id != bot_id:
        reply_text = (reply.text or reply.caption or "").strip()
        header += f"\nОтвет на сообщение {describe_user(reply.from_user)}"
        if reply_text:
            header += f": «{reply_text[:500]}»"
    return header


async def gather_context(svc: Services, topic: Topic, query: str, chat_id: int) -> tuple[str, str]:
    """(найденные данные, блок о текущем моменте) для выбранной темы."""
    # Chroma и ONNX-эмбеддинги синхронные — в отдельном потоке, чтобы не блокировать бота
    if topic is Topic.TG:
        context_text = await asyncio.to_thread(svc.retriever.tainted_grail, query)
        info = today_info()
        if chat_id in svc.settings.tg_priority_chats:
            info += "\n" + texts.TG_CHAT_NOTE
        return context_text, info
    context_text = await asyncio.to_thread(svc.retriever.genshin, query)
    banners = await asyncio.to_thread(svc.banners.load)
    return context_text, current_game_info(banners)  # молитвы/события Genshin


async def ask_claude(svc: Services, history: list[dict], question: str, context_text: str,
                     info: str) -> str:
    response = await svc.claude.messages.create(
        model=svc.settings.claude_model,
        max_tokens=MAX_TOKENS,
        system=svc.system,
        messages=build_messages(history, question, context_text, info),
    )
    u = response.usage
    log.info("Токены: вход %s, из кэша %s, в кэш %s, выход %s", u.input_tokens,
             u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens)
    return "".join(b.text for b in response.content if b.type == "text").strip()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    # В группах отвечаем только если упомянули или ответили на сообщение бота
    if not is_addressed_to_bot(msg, context.bot.username):
        return
    if not await check_access(update, context):
        return

    query = strip_bot_mention(msg.text, context.bot.username)
    if not query:
        return

    svc = get_services(context)
    chat_id = update.effective_chat.id
    key = (chat_id, update.effective_user.id)
    log.info("[%s] thread=%s: %s", describe_user(update.effective_user), msg.message_thread_id, query[:60])

    # IP прокси вне подсети (периодическая проверка) или прокси не отвечает прямо сейчас
    if not await svc.akasha.available():
        await svc.reply(msg, random.choice(texts.AKASHA_DOWN))
        return

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing",
                                           message_thread_id=msg.message_thread_id)
    except TelegramError as e:
        log.warning("send_chat_action не удался (%r) — отвечаю без статуса «печатает»", e)

    question = f"{question_header(msg, context.bot.id)}\n\nВопрос: {query}"
    try:
        topic = detect_topic(query, chat_id, svc.dialogs.last_topic(key),
                             svc.settings.tg_priority_chats,
                             lambda q: bool(svc.title_index.find(q)))
        context_text, info = await gather_context(svc, topic, query, chat_id)
        answer = await ask_claude(svc, svc.dialogs.history(key), question, context_text, info)
        svc.dialogs.remember(key, question, answer, topic)
        await svc.reply(msg, answer)
    except anthropic.APIConnectionError as e:
        # Claude недоступен по сети (в т.ч. прокси упал между проверкой и запросом)
        log.error("Claude API недоступен: %r", e)
        await svc.reply(msg, random.choice(texts.AKASHA_DOWN))
    except Exception:
        log.exception("Ошибка обработки сообщения")
        await svc.reply(msg, texts.GENERIC_ERROR)
