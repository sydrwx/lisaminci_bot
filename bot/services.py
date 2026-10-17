"""Зависимости бота: создаются один раз при старте и доступны обработчикам через bot_data."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic
from telegram import Message
from telegram.ext import ContextTypes

from bot import prompt
from bot.access import Access
from bot.akasha import Akasha
from bot.config import CLAUDE_BASE_URL, Settings
from bot.dialogs import Dialogs
from bot.game_info import BannerStore
from bot.messaging import SentMessages, send_reply
from bot.pray import PrayStore
from bot.retrieval import Retriever, load_chat_topics
from bot.tasks import TaskStore
from bot.titles import TitleIndex, collection_titles
from common.db import TG_COLLECTION, get_collection, get_embedding_function
from common.state import StateStore

log = logging.getLogger(__name__)

BOT_DATA_KEY = "services"


@dataclass
class Services:
    settings: Settings
    claude: anthropic.AsyncAnthropic
    system: list[dict]          # системный промпт (блоки с cache_control)
    system_admin: list[dict]    # то же с пометкой «пишет администратор»
    collection: object          # коллекция Genshin в Chroma
    title_index: TitleIndex
    retriever: Retriever
    banners: BannerStore
    dialogs: Dialogs
    sent: SentMessages
    pray: PrayStore
    tasks: TaskStore
    access: Access
    akasha: Akasha

    async def reply(self, msg: Message, text: str, **kwargs) -> Message | None:
        """Ответ в тред исходного сообщения; id ответа запоминается для /delete."""
        kwargs.setdefault("message_thread_id", msg.message_thread_id)
        return await send_reply(msg, text, self.sent, **kwargs)


def get_services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.bot_data[BOT_DATA_KEY]


def _claude_client(settings: Settings) -> anthropic.AsyncAnthropic:
    # При ANTHROPIC_PROXY — через HTTP-прокси (CONNECT), TLS до api.anthropic.com сохраняется
    proxy = settings.anthropic_proxy
    log.info("Claude API: %s", f"через прокси {proxy}" if proxy else "напрямую")
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_key,
        base_url=CLAUDE_BASE_URL,
        **({"http_client": anthropic.DefaultAsyncHttpxClient(proxy=proxy)} if proxy else {}),
    )


def build_services(settings: Settings) -> Services:
    """Подключения к Claude и ChromaDB, состояние из файлов, первая проверка IP."""
    claude = _claude_client(settings)

    log.info("Подключаюсь к ChromaDB...")
    collection = get_collection()
    embed = get_embedding_function()
    log.info("Чанков в базе: %d", collection.count())
    try:
        tg_collection = get_collection(name=TG_COLLECTION)
        log.info("Чанков по «Осквернённому Граалю»: %d", tg_collection.count())
    except Exception as e:
        tg_collection = None
        log.warning("Коллекция %s недоступна (%s) — вопросы по Tainted Grail без базы", TG_COLLECTION, e)

    title_index = TitleIndex(lambda: collection_titles(collection))
    retriever = Retriever(collection, tg_collection, embed, title_index,
                          load_chat_topics(settings.kb_file))

    if settings.allowed_chats:
        log.info("Разрешённые чаты: %s", sorted(settings.allowed_chats))
        log.info("Личка: %s", ", ".join(f"{uid} ({label})" if label else str(uid)
                                        for uid, label in settings.allowed_users.items()) or "никому")
    else:
        log.warning("ALLOWED_CHATS и TG_CHAT не заданы — бот отвечает в любых чатах!")

    akasha = Akasha(settings.anthropic_proxy, settings.proxy_check_timeout,
                    settings.ip_check_interval, settings.ip_recheck_interval)
    akasha.refresh_ip()

    state = StateStore.connect()

    pray = PrayStore(state, settings.pray_bonus_users, settings.pray_daily_limit)
    system_prompt = prompt.build_system_prompt(prompt.load_private_lines(settings.private_prompt_file))
    return Services(
        settings=settings,
        claude=claude,
        system=prompt.system_blocks(system_prompt),
        system_admin=prompt.system_blocks(system_prompt, prompt.load_admin_note(settings.admin_prompt_file)),
        collection=collection,
        title_index=title_index,
        retriever=retriever,
        banners=BannerStore(state),
        dialogs=Dialogs(state),
        sent=SentMessages(state),
        pray=pray,
        tasks=TaskStore(state, pray),
        access=Access(settings.allowed_chats, settings.allowed_users),
        akasha=akasha,
    )
