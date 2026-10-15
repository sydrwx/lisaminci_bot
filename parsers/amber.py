"""
parsers/amber.py — данные из файлов игры через Project Amber (gi.yatta.moe), на русском

Персонажи (профиль, таланты, созвездия), оружие (пассивки R1/R5), наборы артефактов.
Обновляется быстрее вики: новые персонажи и оружие появляются в день выхода версии.
"""

import re
import time
import logging
from datetime import datetime, timezone

import requests

API = "https://gi.yatta.moe/api/v2/ru"
SOURCE = "amber"
REQUEST_DELAY = 0.2
USER_AGENT = "LisaGenshinBot-updater/1.0 (Telegram bot knowledge base sync)"

ELEMENTS = {
    "Fire": "Пиро", "Water": "Гидро", "Wind": "Анемо", "Electric": "Электро",
    "Grass": "Дендро", "Ice": "Крио", "Rock": "Гео",
}
REGIONS = {
    "MONDSTADT": "Мондштадт", "LIYUE": "Ли Юэ", "INAZUMA": "Инадзума", "SUMERU": "Сумеру",
    "FONTAINE": "Фонтейн", "NATLAN": "Натлан", "SNEZHNAYA": "Снежная", "SNEZHNAYA_STAR": "Снежная",
    "NODKRAI": "Нод-Край", "NODKRAI_ZIBAI": "Нод-Край", "FATUI": "Фатуи",
    "OMNI_SCOURGE": "Космический катаклизм", "RANGER": "Странник", "MAINACTOR": "Путешественник",
    "HVISION": "Сумеру",
}
TALENT_TYPES = {0: "Обычная атака", 1: "Элементальный навык", 2: "Взрыв стихии", 3: "Пассивный талант"}

log = logging.getLogger("updater.amber")
session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def get(path: str) -> dict:
    for attempt in range(4):
        try:
            r = session.get(f"{API}/{path}", timeout=60)
            r.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return r.json()["data"]
        except (requests.RequestException, ValueError, KeyError) as e:
            if attempt == 3:
                raise
            log.warning(f"Amber {path}: {e}, повтор")
            time.sleep(3 * (attempt + 1))


def clean(text: str | None) -> str:
    """Убрать игровую разметку: <color>, {LINK#..}, {LAYOUT_..#..}, \\n."""
    if not text:
        return ""
    text = text.replace("\\n", "\n")
    text = re.sub(r"</?color[^>]*>", "", text)
    text = re.sub(r"\{/?LINK[^}]*\}", "", text)
    text = re.sub(r"\{LAYOUT_MOBILE#[^}]*\}|\{LAYOUT_PS#[^}]*\}", "", text)
    text = re.sub(r"\{LAYOUT_PC#([^}]*)\}", r"\1", text)
    text = re.sub(r"\{[A-Z_]+#([^}]*)\}", r"\1", text)
    return text.strip()


def doc(id_: str, title: str, text: str) -> dict:
    return {"id": f"{SOURCE}__{id_}", "title": title, "text": text.strip()}


# ═══════════════════════════════════════════════════════════════════════════════

def character_docs(item: dict, detail: dict, types: dict) -> list[dict]:
    name = item["name"]
    cid = item["id"]
    fetter = detail.get("fetter") or {}
    release = item.get("release")
    rel = datetime.fromtimestamp(release, timezone.utc) if release else None
    status = ""
    if rel and rel > datetime.now(timezone.utc):
        status = f"\nЕщё не вышел, ожидается {rel:%Y-%m-%d}"

    bday = item.get("birthday") or []
    profile = [
        f"# {name} — данные из игры",
        f"Редкость: {item.get('rank')}★",
        f"Элемент: {ELEMENTS.get(item.get('element'), item.get('element') or '—')}",
        f"Оружие: {types.get(item.get('weaponType'), item.get('weaponType'))}",
        f"Регион: {REGIONS.get(item.get('region'), item.get('region') or '—')}",
        f"Принадлежность: {fetter.get('native') or '—'}",
        f"Созвездие: {fetter.get('constellation') or '—'}",
        f"Титул: {fetter.get('title') or '—'}",
        f"День рождения: {bday[1]:02d}.{bday[0]:02d}" if len(bday) == 2 and bday[0] else "",
        f"Дата выхода: {rel:%Y-%m-%d}" if rel else "",
        f"Описание: {clean(fetter.get('detail'))}",
    ]
    docs = [doc(f"char__{cid}__profile", name, "\n".join(l for l in profile if l) + status)]

    for key, t in (detail.get("talent") or {}).items():
        kind = TALENT_TYPES.get(t.get("type"), "Талант")
        docs.append(doc(
            f"char__{cid}__talent__{key}", name,
            f"# {name} — {kind}: {t.get('name')}\n{clean(t.get('description'))}",
        ))

    for i, (key, c) in enumerate(sorted((detail.get("constellation") or {}).items(),
                                        key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0), 1):
        docs.append(doc(
            f"char__{cid}__const__{key}", name,
            f"# {name} — созвездие C{i}: {c.get('name')}\n{clean(c.get('description'))}",
        ))
    return docs


def weapon_docs(item: dict, detail: dict, types: dict, props: dict) -> list[dict]:
    name = item["name"]
    lines = [
        f"# {name} — оружие, данные из игры",
        f"Редкость: {item.get('rank')}★",
        f"Тип: {types.get(item.get('type'), item.get('type'))}",
        f"Доп. характеристика: {props.get(item.get('specialProp'), item.get('specialProp'))}",
        f"Описание: {clean(detail.get('description'))}",
    ]
    for affix in (detail.get("affix") or {}).values():
        upgrades = affix.get("upgrade") or {}
        lines.append(f"Пассивный эффект «{affix.get('name')}»:")
        if "0" in upgrades:
            lines.append(f"R1: {clean(upgrades['0'])}")
        if "4" in upgrades:
            lines.append(f"R5: {clean(upgrades['4'])}")
    return [doc(f"weapon__{item['id']}", name, "\n".join(lines))]


def artifact_docs(item: dict) -> list[dict]:
    name = item["name"]
    levels = item.get("levelList") or []
    affixes = list((item.get("affixList") or {}).values())
    lines = [f"# {name} — набор артефактов, данные из игры"]
    if levels:
        lines.append(f"Редкость: {'–'.join(str(l) for l in levels)}★")
    for count, text in zip((2, 4) if len(affixes) > 1 else (1,), affixes):
        lines.append(f"{count} предмета: {clean(text)}" if count > 1 else f"1 предмет: {clean(text)}")
    return [doc(f"artifact__{item['id']}", name, "\n".join(lines))]


def collect() -> list[dict]:
    """Все документы источника: [{id, title, text}]."""
    docs: list[dict] = []

    avatars = get("avatar")
    for item in avatars["items"].values():
        if not item.get("name"):
            continue
        try:
            docs += character_docs(item, get(f"avatar/{item['id']}"), avatars.get("types", {}))
        except Exception as e:
            log.warning(f"Amber: персонаж {item.get('name')}: {e}")
    log.info(f"Amber: персонажей {len(avatars['items'])}")

    weapons = get("weapon")
    n = 0
    for item in weapons["items"].values():
        if item.get("isWeaponSkin") or not item.get("name"):
            continue
        try:
            docs += weapon_docs(item, get(f"weapon/{item['id']}"),
                                weapons.get("types", {}), weapons.get("props", {}))
            n += 1
        except Exception as e:
            log.warning(f"Amber: оружие {item.get('name')}: {e}")
    log.info(f"Amber: оружия {n}")

    artifacts = get("reliquary")
    for item in artifacts["items"].values():
        if item.get("name"):
            docs += artifact_docs(item)
    log.info(f"Amber: наборов артефактов {len(artifacts['items'])}")
    return docs
