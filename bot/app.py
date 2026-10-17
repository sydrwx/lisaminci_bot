"""Сборка и запуск Telegram-приложения."""

from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand
from telegram.ext import Application

from bot import handlers
from bot.akasha import watch_network
from bot.config import BOT_NAME, Settings
from bot.handlers.tasks import remind_daily
from bot.services import BOT_DATA_KEY, Services, build_services

log = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "edited_message", "channel_post", "my_chat_member", "callback_query"]


def setup_logging() -> None:
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _post_init(app: Application) -> None:
    svc: Services = app.bot_data[BOT_DATA_KEY]
    # Фоновые задачи живут, пока работает цикл событий бота; ссылки храним, чтобы их не собрал GC
    app.bot_data["tasks"] = [
        asyncio.create_task(svc.akasha.watch_ip()),
        asyncio.create_task(watch_network(app.bot, svc.akasha, svc.settings.net_check_interval)),
        asyncio.create_task(remind_daily(app.bot, svc)),
    ]
    await asyncio.to_thread(svc.title_index.find, "")  # прогреть индекс названий
    try:
        # Вместо команд — кнопки меню; в списке команд Telegram остаётся только /menu
        await app.bot.set_my_commands([BotCommand("menu", "Меню Лизы")])
    except Exception as e:
        log.warning("Не удалось обновить список команд: %s", e)
    try:
        if (await app.bot.get_my_name()).name != BOT_NAME:
            await app.bot.set_my_name(BOT_NAME)
            log.info("Имя бота изменено на «%s»", BOT_NAME)
    except Exception as e:
        log.warning("Не удалось сменить имя бота: %s", e)


def build_application(services: Services) -> Application:
    app = (
        Application.builder()
        .token(services.settings.bot_token)
        .post_init(_post_init)
        .concurrent_updates(True)  # несколько пользователей обслуживаются параллельно
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(15)
        .build()
    )
    app.bot_data[BOT_DATA_KEY] = services
    handlers.register(app)
    return app


def main() -> None:
    setup_logging()
    settings = Settings.from_env()
    app = build_application(build_services(settings))
    log.info("Бот запущен.")
    app.run_polling(drop_pending_updates=True, allowed_updates=ALLOWED_UPDATES)
