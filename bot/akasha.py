"""Доступность Claude API («Акаша») и фоновые проверки связи.

Claude используется, только пока внешний IP прокси в ALLOWED_SUBNET и сам прокси отвечает.
Бот при этом не падает — просто отвечает репликами AKASHA_DOWN.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from urllib.parse import urlparse

from telegram import Bot
from telegram.error import TelegramError

from common.ipcheck import IPCheckError, check_ip

log = logging.getLogger(__name__)


class Akasha:
    def __init__(self, proxy: str | None, proxy_timeout: float,
                 check_interval: int, recheck_interval: int):
        self.proxy = proxy
        self.proxy_timeout = proxy_timeout
        self.check_interval = check_interval
        self.recheck_interval = recheck_interval
        self.ip_ok = False  # результат последней проверки IP

    def refresh_ip(self) -> bool:
        """Проверить внешний IP прокси (синхронно, сетевые запросы к сервисам IP)."""
        try:
            check_ip(self.proxy)
            ok = True
        except IPCheckError as e:
            log.warning("%s. Claude API отключён.", e)
            ok = False
        if ok != self.ip_ok:
            log.info("Акаша %s", "доступен" if ok else "НЕДОСТУПЕН")
        self.ip_ok = ok
        return ok

    async def proxy_error(self) -> str | None:
        """None, если TCP-соединение с прокси открывается, иначе текст ошибки."""
        if not self.proxy:
            return None
        u = urlparse(self.proxy)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), self.proxy_timeout)
        except (OSError, TimeoutError) as e:
            return f"{host}:{port} — {e!r}"
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return None

    async def available(self) -> bool:
        """Перед каждым запросом к Claude: IP в подсети и прокси отвечает прямо сейчас."""
        if not self.ip_ok:
            return False
        err = await self.proxy_error()
        if err:
            log.warning("Прокси %s недоступен — отвечаю «Акаша недоступен»", err)
        return err is None

    async def watch_ip(self) -> None:
        """Периодическая проверка IP; при сбое — чаще, чтобы быстрее включить Claude обратно."""
        while True:
            await asyncio.sleep(self.check_interval if self.ip_ok else self.recheck_interval)
            await asyncio.to_thread(self.refresh_ip)


class _Outage:
    """Пишет в лог начало и конец недоступности (только смену состояния)."""

    def __init__(self, lost: str, restored: str):
        self.lost, self.restored = lost, restored
        self.since: float | None = None

    def update(self, error: str | None) -> None:
        if error and self.since is None:
            self.since = time.monotonic()
            log.warning(self.lost, error)
        elif not error and self.since is not None:
            log.info(self.restored, time.monotonic() - self.since)
            self.since = None


async def watch_network(bot: Bot, akasha: Akasha, interval: int) -> None:
    """Фоновая проверка связи с Telegram и прокси.

    Без неё обрыв связи не виден: PTB при таймаутах getUpdates молча повторяет запрос
    (лог DEBUG), а прокси проверяется только при входящем сообщении.
    """
    telegram = _Outage("Связь с Telegram потеряна (%s): сообщения не доходят до Лизы, "
                       "ответить на них (даже «Акаша недоступен») невозможно",
                       "Связь с Telegram восстановлена (не было %.0f с)")
    proxy = _Outage("Прокси %s недоступен — на вопросы отвечаю «Акаша недоступен»",
                    "Прокси снова доступен (не было %.0f с)")
    while True:
        await asyncio.sleep(interval)
        try:
            await bot.get_me(connect_timeout=10, read_timeout=10)
            telegram.update(None)
        except TelegramError as e:
            telegram.update(repr(e))
        proxy.update(await akasha.proxy_error())
