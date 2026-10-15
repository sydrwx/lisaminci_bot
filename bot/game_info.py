"""Блок «Сейчас в игре»: дата, молитвы и события из state/banners.json (paimon.moe)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta, timezone

log = logging.getLogger(__name__)

SERVER_TZ = timezone(timedelta(hours=8))  # время в paimon.moe — сервер Азия
CHARACTER_BANNER = "Молитва события персонажа"


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SERVER_TZ)


def is_active(item: dict, now: datetime) -> bool:
    return parse_time(item["start"]) <= now <= parse_time(item["end"])


class BannerStore:
    """state/banners.json (пишет updater); перечитывается при изменении файла."""

    def __init__(self, path: str):
        self.path = path
        self._mtime = 0.0
        self._data: dict = {}

    def load(self) -> dict:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return {}
        if mtime != self._mtime:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
                self._mtime = mtime
            except (OSError, ValueError) as e:
                log.warning("Не удалось прочитать %s: %s", self.path, e)
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
