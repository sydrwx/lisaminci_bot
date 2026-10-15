"""Блок «Сейчас в игре»: дата, молитвы и события paimon.moe (пишет updater, запись banners:current)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta, timezone

from common.state import StateStore

log = logging.getLogger(__name__)

SERVER_TZ = timezone(timedelta(hours=8))  # время в paimon.moe — сервер Азия
CHARACTER_BANNER = "Молитва события персонажа"
BANNERS_TTL = 300  # сек: updater обновляет данные раз в сутки, чаще ходить в базу незачем


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SERVER_TZ)


def is_active(item: dict, now: datetime) -> bool:
    return parse_time(item["start"]) <= now <= parse_time(item["end"])


class BannerStore:
    """Снимок молитв и событий из базы с кэшем на ttl секунд.

    Синхронный (HTTP к Chroma) — из обработчиков вызывать через asyncio.to_thread.
    """

    KIND, KEY = "banners", "current"

    def __init__(self, store: StateStore, ttl: float = BANNERS_TTL):
        self.store = store
        self.ttl = ttl
        self._loaded_at: float | None = None
        self._data: dict = {}

    def load(self) -> dict:
        if self._loaded_at is not None and time.monotonic() - self._loaded_at < self.ttl:
            return self._data
        try:
            self._data = self.store.get(self.KIND, self.KEY, {})
        except Exception as e:  # база недоступна — отдаём последний снимок
            log.warning("Не удалось прочитать молитвы и события из базы: %r", e)
        self._loaded_at = time.monotonic()
        return self._data

    def current_character_banner(self, now: datetime | None = None) -> dict | None:
        now = now or datetime.now(SERVER_TZ)
        for b in self.load().get("banners", []):
            if b["type"] == CHARACTER_BANNER and is_active(b, now):
                return b
        return None


def _banner_line(b: dict, until: bool) -> str:
    rare = f"; 4★: {', '.join(b['featured_rare'])}" if b.get("featured_rare") else ""
    when = f"до {b['end'][:16]}" if until else f"с {b['start'][:16]} до {b['end'][:16]}"
    ver = f", версия {b['version']}" if b.get("version") else ""
    return f"- {b['type']} «{b['name']}»{ver}, {when}: 5★ {', '.join(b['featured'])}{rare}"


def current_game_info(data: dict, now: datetime | None = None) -> str:
    """Дата и текущие/ближайшие молитвы и события — прикладывается к каждому вопросу о Genshin."""
    now = now or datetime.now(SERVER_TZ)
    lines = ["## Сейчас в игре", f"Сегодня: {now:%Y-%m-%d %H:%M} (UTC+8, сервер Азия)"]
    if not data:
        return "\n".join(lines)

    banners = data.get("banners", [])
    current = [b for b in banners if is_active(b, now)]
    upcoming = [b for b in banners if parse_time(b["start"]) > now]
    lines.append(f"Данные о молитвах: paimon.moe, обновлено {data.get('generated', '')[:10]}")
    lines.append("Текущие молитвы:")
    lines += [_banner_line(b, True) for b in current] or ["- нет данных"]
    lines.append("Следующие молитвы:")
    lines += [_banner_line(b, False) for b in upcoming] or ["- ещё не объявлены"]

    all_events = data.get("events", [])
    events = [e for e in all_events if is_active(e, now)]
    if events:
        lines.append("Текущие события:")
        lines += [f"- {e['name']} (до {e['end'][:10]})" for e in events[:12]]
    soon = sorted((e for e in all_events if parse_time(e["start"]) > now), key=lambda e: e["start"])
    if soon:
        lines.append("Скоро:")
        lines += [f"- {e['name']} (с {e['start'][:10]})" for e in soon[:6]]
    return "\n".join(lines)


def today_info() -> str:
    """Для вопросов о настолке: только дата."""
    return f"Сегодня: {datetime.now(UTC):%Y-%m-%d}"
