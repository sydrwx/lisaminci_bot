"""
src_paimon.py — молитвы (баннеры) и события из paimon.moe

Данные paimon.moe лежат в его открытом репозитории (github.com/MadeBaruna/paimon-moe),
сайт собирается из них. Берём:
    banners.js   — все молитвы события, оружия и хроник (даты, 5★/4★, версия);
    timeline.js  — текущие и ближайшие события;
    characters.js / weaponList.js + locales/items/ru.json — русские названия.

Результат:
    - state/banners.json — актуальные молитвы/события; бот читает его на каждый запрос;
    - документы для ChromaDB: история молитв по персонажам/оружию и по версиям.
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta, timezone

import json5
import requests

RAW = "https://raw.githubusercontent.com/MadeBaruna/paimon-moe/main/src"
SOURCE = "paimon"
USER_AGENT = "LisaGenshinBot-updater/1.0 (Telegram bot knowledge base sync)"
# Время в paimon.moe — серверное; без timezoneDependent это время сервера Азия (UTC+8)
SERVER_TZ = timezone(timedelta(hours=8))
BANNER_TYPES = {"characters": "Молитва события персонажа", "weapons": "Молитва события оружия",
                "chronicled": "Молитва хроник"}
KEEP_DAYS = 45  # в banners.json — молитвы/события, закончившиеся не раньше чем столько дней назад

log = logging.getLogger("updater.paimon")


def fetch(path: str) -> str:
    r = requests.get(f"{RAW}/{path}", headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.text


def parse_js_literal(js: str):
    """`export const x = {...};` → Python-объект (JS-литерал: комментарии, одинарные кавычки)."""
    body = js[js.index("=", js.index("export const")) + 1:].strip().rstrip(";")
    return json5.loads(body)


def load_names() -> dict[str, str]:
    """id paimon.moe → русское название (или английское, если перевода нет)."""
    ru = json.loads(fetch("locales/items/ru.json"))
    names: dict[str, str] = {}
    for js in (fetch("data/characters.js"), fetch("data/weaponList.js")):
        for m in re.finditer(r"id: '([^']+)',\s*name: (['\"])(.+?)\2", js):
            names[m.group(1)] = m.group(3)
        for m in re.finditer(r"name: (['\"])(.+?)\1,\s*id: '([^']+)'", js):
            names[m.group(3)] = m.group(2)
    return {id_: ru.get(en, en) for id_, en in names.items()}


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SERVER_TZ)


def collect(state_path: str) -> list[dict]:
    names = load_names()
    banners = parse_js_literal(fetch("data/banners.js"))
    timeline = parse_js_literal(fetch("data/timeline.js"))
    now = datetime.now(SERVER_TZ)
    name = lambda id_: names.get(id_, id_.replace("_", " ").title())

    all_banners = []
    for kind, label in BANNER_TYPES.items():
        for b in banners.get(kind, []):
            all_banners.append({
                "type": label,
                "name": b.get("name"),
                "start": b["start"],
                "end": b["end"],
                "version": b.get("version"),
                "featured": [name(i) for i in b.get("featured", [])],
                # у молитвы хроник featuredRare — весь пул, не «4★ с повышенным шансом»
                "featured_rare": [name(i) for i in b.get("featuredRare", [])] if kind != "chronicled" else [],
                "server_time": bool(b.get("timezoneDependent")),
            })
    # Двойные молитвы (две 5★ на одних датах) в paimon.moe — одна запись с двумя featured
    all_banners.sort(key=lambda b: b["start"])

    events = []
    for group in timeline:
        for e in group if isinstance(group, list) else [group]:
            events.append({
                "name": e.get("name"),
                "start": e["start"],
                "end": e["end"],
                "description": (e.get("description") or "").strip(),
                "url": e.get("url"),
            })

    # ── state/banners.json для бота ─────────────────────────────────────────
    cutoff = now - timedelta(days=KEEP_DAYS)
    state = {
        "source": "paimon.moe",
        "generated": now.isoformat(),
        "timezone_note": "время сервера Азия (UTC+8); для Европы — на 7 ч раньше",
        "banners": [b for b in all_banners if parse_time(b["end"]) >= cutoff],
        "events": [e for e in events if parse_time(e["end"]) >= cutoff],
    }
    with open(state_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(state_path + ".tmp", state_path)
    log.info(f"paimon.moe: молитв {len(all_banners)}, актуальных {len(state['banners'])}, событий {len(events)}")

    # ── документы для базы ──────────────────────────────────────────────────
    docs: list[dict] = []
    fmt = lambda b: (f"{b['start'][:10]} – {b['end'][:10]}: «{b['name']}» ({b['type'].lower()}"
                     f"{', версия ' + b['version'] if b.get('version') else ''})")

    # История по каждому персонажу/оружию: когда был в молитвах (5★ и 4★ с повышенным шансом)
    history: dict[str, list[str]] = {}
    for b in all_banners:
        for item in b["featured"]:
            others = [x for x in b["featured"] if x != item]
            history.setdefault(item, []).append(
                fmt(b) + (f" — 5★ вместе с {', '.join(others)}" if others else " — 5★"))
        for item in b["featured_rare"]:
            history.setdefault(item, []).append(fmt(b) + " — 4★ с повышенным шансом")
    for item, rows in history.items():
        docs.append({
            "id": f"{SOURCE}__history__{item}",
            "title": item,
            "text": f"# {item} — история молитв (paimon.moe)\n"
                    f"Появлялся в молитвах {len(rows)} раз(а):\n" + "\n".join(f"- {r}" for r in rows),
        })

    # Молитвы по версиям
    by_version: dict[str, list[dict]] = {}
    for b in all_banners:
        if b.get("version"):
            by_version.setdefault(b["version"], []).append(b)
    for version, items in by_version.items():
        rows = []
        for b in items:
            rare = f"; 4★: {', '.join(b['featured_rare'])}" if b["featured_rare"] else ""
            rows.append(f"- {fmt(b)}: 5★ {', '.join(b['featured'])}{rare}")
        docs.append({
            "id": f"{SOURCE}__version__{version}",
            "title": f"Молитвы версии {version}",
            "text": f"# Молитвы версии {version} (paimon.moe)\n" + "\n".join(rows),
        })

    for e in events:
        docs.append({
            "id": f"{SOURCE}__event__{e['name']}__{e['start'][:10]}",
            "title": e["name"],
            "text": f"# Событие: {e['name']} (paimon.moe)\n"
                    f"Даты: {e['start'][:16]} – {e['end'][:16]}\n{e['description']}".strip(),
        })
    return docs
