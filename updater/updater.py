"""
updater/updater.py — обновление базы знаний ChromaDB: при старте и раз в сутки

Источники:
    wiki   — Genshin Impact Вики (Fandom, ru): статьи, включая данные инфобоксов;
    amber  — Project Amber (gi.yatta.moe): данные из файлов игры на русском —
             персонажи (таланты, созвездия), оружие, артефакты (parsers/amber.py);
    paimon — paimon.moe: молитвы и события; актуальные пишутся в state/banners.json
             для бота, история — в базу (parsers/paimon.py);
    tg     — настольная игра «Осквернённый Грааль»: правила (PDF), путеводитель,
             лор — в отдельную коллекцию TG_COLLECTION (parsers/tg.py).

Режимы:
    python -m updater.updater                 # демон: синхронизация сразу и затем раз в сутки в UPDATE_TIME (UTC)
    python -m updater.updater --once          # одна синхронизация и выход
    python -m updater.updater --full          # то же, но вики — полностью
    python -m updater.updater --once --only amber paimon   # только выбранные источники

Вики синхронизируется инкрементально по recentchanges MediaWiki API. Полная синхронизация
через allpages — если прошлой не было, она старше RC_MAX_AGE_DAYS или сменилась
обработка статей (WIKI_SCHEMA).
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta, timezone

import hashlib

import requests

from parsers import amber as src_amber, paimon as src_paimon, tg as src_tg
from common.db import COLLECTION, TG_COLLECTION, get_collection
from parsers.wiki_xml import clean_wikitext, chunk_article, MIN_LENGTH, BATCH_SIZE

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("updater")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ─── Конфигурация ────────────────────────────────────────────────────────────
WIKI_API        = os.getenv("WIKI_API", "https://genshin-impact.fandom.com/ru/api.php")
# Отдельное состояние на коллекцию: новая коллекция начинается с полной синхронизации
STATE_FILE      = os.getenv("STATE_FILE", f"./state/updater_state_{COLLECTION}.json")
UPDATE_TIME     = os.getenv("UPDATE_TIME", "01:00")       # HH:MM, UTC (04:00 МСК)
RETRY_MINUTES   = int(os.getenv("RETRY_MINUTES", "60"))   # повтор после ошибки
RC_MAX_AGE_DAYS = int(os.getenv("RC_MAX_AGE_DAYS", "30"))
BANNERS_FILE    = os.getenv("BANNERS_FILE", "./state/banners.json")
WIKI_SCHEMA     = 2  # версия обработки статей; при изменении — полная пересинхронизация вики
SOURCES         = ("wiki", "amber", "paimon", "tg")
REQUEST_DELAY   = 0.3  # пауза между запросами к API, сек
USER_AGENT      = "LisaGenshinBot-updater/1.0 (Telegram bot knowledge base sync)"
# ─────────────────────────────────────────────────────────────────────────────

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


# ═══════════════════════════════════════════════════════════════════════════════
#  Состояние
# ═══════════════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """{источник: {"last_sync": iso, "stats": {...}, ...}}."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    if "last_sync" in state:  # старый формат: только вики
        return {"wiki": {"last_sync": state["last_sync"], "schema": 1}}
    return state


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ═══════════════════════════════════════════════════════════════════════════════
#  MediaWiki API
# ═══════════════════════════════════════════════════════════════════════════════

def api_query(params: dict):
    """Итерироваться по ответам API с учётом continue."""
    params = {**params, "action": "query", "format": "json", "formatversion": "2"}
    cont: dict = {}
    while True:
        for attempt in range(5):
            try:
                r = session.get(WIKI_API, params={**params, **cont}, timeout=60)
                r.raise_for_status()
                data = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                if attempt == 4:
                    raise
                log.warning(f"Ошибка API ({e}), повтор через {5 * (attempt + 1)} с")
                time.sleep(5 * (attempt + 1))
        if "error" in data:
            raise RuntimeError(f"MediaWiki API error: {data['error']}")
        yield data
        if "continue" not in data:
            return
        cont = data["continue"]
        time.sleep(REQUEST_DELAY)


def page_content(page: dict) -> str | None:
    revs = page.get("revisions")
    if not revs:
        return None
    return revs[0].get("slots", {}).get("main", {}).get("content")


def changed_titles_since(since: datetime) -> set[str]:
    """Названия статей (ns=0), изменённых, созданных, удалённых или перемещённых после since."""
    titles: set[str] = set()
    for data in api_query({
        "list":        "recentchanges",
        "rcnamespace": "0",
        "rctype":      "edit|new|log",
        "rcprop":      "title|loginfo",
        "rcend":       since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rclimit":     "500",
    }):
        for rc in data["query"]["recentchanges"]:
            titles.add(rc["title"])
            target = rc.get("logparams", {}).get("target_title")
            if target:
                titles.add(target)
    return titles


def fetch_pages(titles: list[str]):
    """Выдать пачки {title: content | None}; None — страницы нет / не статья."""
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        pages: dict[str, str | None] = {t: None for t in batch}
        for data in api_query({
            "prop":    "revisions",
            "rvprop":  "content",
            "rvslots": "main",
            "titles":  "|".join(batch),
        }):
            query = data.get("query", {})
            # API нормализует названия (регистр первой буквы и т.п.)
            norm = {n["to"]: n["from"] for n in query.get("normalized", [])}
            for page in query.get("pages", []):
                title = norm.get(page["title"], page["title"])
                if page.get("ns") == 0 and not page.get("missing"):
                    content = page_content(page)
                    if content is not None:
                        pages[title] = content
        yield pages


def fetch_all_pages():
    """Выдать пачки {title: content} всех статей основного пространства, кроме редиректов."""
    for data in api_query({
        "generator":         "allpages",
        "gapnamespace":      "0",
        "gapfilterredir":    "nonredirects",
        "gaplimit":          "50",
        "prop":              "revisions",
        "rvprop":            "content",
        "rvslots":           "main",
    }):
        pages = {}
        for page in data.get("query", {}).get("pages", []):
            content = page_content(page)
            if content is not None:
                pages[page["title"]] = content
        if pages:
            yield pages


# ═══════════════════════════════════════════════════════════════════════════════
#  Запись в ChromaDB
# ═══════════════════════════════════════════════════════════════════════════════

def is_article(content: str | None) -> bool:
    if not content:
        return False
    head = content.strip().lower()
    return not (head.startswith("#redirect") or head.startswith("#перенаправление"))


WIKI_WHERE = {"source": "wiki"}


def apply_pages(collection, pages: dict[str, str | None]) -> tuple[set[str], int]:
    """Перезаписать чанки статей. Возвращает (оставшиеся в базе названия, число чанков)."""
    chunks = []
    kept: set[str] = set()
    for title, content in pages.items():
        if not is_article(content):
            continue
        clean = clean_wikitext(content)
        if len(clean) < MIN_LENGTH:
            continue
        kept.add(title)
        chunks.extend(chunk_article(title, clean))

    # Сначала удаляем старые чанки: статья могла стать короче, удалиться или стать редиректом.
    # Только вики — у Amber/paimon могут быть документы с тем же названием
    collection.delete(where={"$and": [{"title": {"$in": list(pages)}}, WIKI_WHERE]})

    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        collection.upsert(
            ids=[f"{c['title']}__chunk{c['chunk_id']}" for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[{"title": c["title"], "chunk_id": c["chunk_id"], "source": "wiki"} for c in batch],
        )
    return kept, len(chunks)


def iter_metadatas(collection, where: dict | None = None):
    offset, limit = 0, 5000
    while True:
        got = collection.get(where=where, include=["metadatas"], limit=limit, offset=offset)
        yield from zip(got["ids"], got["metadatas"])
        if len(got["ids"]) < limit:
            return
        offset += limit


def full_sync(collection) -> dict:
    log.info("Вики: полная синхронизация, загружаю все статьи...")
    seen: set[str] = set()
    n_pages = n_chunks = 0
    for pages in fetch_all_pages():
        kept, chunks = apply_pages(collection, pages)
        seen |= kept
        n_pages += len(pages)
        n_chunks += chunks
        if n_pages % 1000 < len(pages):
            log.info(f"   обработано статей: {n_pages}, чанков: {n_chunks}")

    # Устаревшие: статьи вики, которых больше нет, и чанки старого формата без source
    stale_ids, stale_titles = [], set()
    for id_, meta in iter_metadatas(collection):
        meta = meta or {}
        src = meta.get("source")
        if src is None or (src == "wiki" and meta.get("title") not in seen):
            stale_ids.append(id_)
            stale_titles.add(meta.get("title"))
    for i in range(0, len(stale_ids), 1000):
        collection.delete(ids=stale_ids[i : i + 1000])
    log.info(f"Вики: удалено устаревших чанков {len(stale_ids)} ({len(stale_titles)} статей)")

    return {"mode": "full", "pages": n_pages, "articles": len(seen),
            "chunks": n_chunks, "removed_chunks": len(stale_ids)}


def incremental_sync(collection, since: datetime) -> dict:
    titles = sorted(changed_titles_since(since))
    log.info(f"Вики: изменённых статей с {since:%Y-%m-%d %H:%M} UTC: {len(titles)}")
    n_kept = n_chunks = 0
    for pages in fetch_pages(titles):
        kept, chunks = apply_pages(collection, pages)
        n_kept += len(kept)
        n_chunks += chunks
    return {"mode": "incremental", "changed": len(titles),
            "updated_articles": n_kept, "chunks": n_chunks,
            "removed_articles": len(titles) - n_kept}


def sync_wiki(collection, prev: dict, started: datetime, force_full: bool) -> dict:
    last = datetime.fromisoformat(prev["last_sync"]) if prev.get("last_sync") else None
    if (force_full or last is None or prev.get("schema") != WIKI_SCHEMA
            or started - last > timedelta(days=RC_MAX_AGE_DAYS)):
        stats = full_sync(collection)
    else:
        # Небольшой нахлёст, чтобы не потерять правки на границе интервала
        stats = incremental_sync(collection, last - timedelta(minutes=10))
    return {"last_sync": started.isoformat(), "schema": WIKI_SCHEMA, "stats": stats}


def doc_hash(d: dict) -> str:
    return hashlib.sha1(f"{d['title']}\n{d['text']}".encode()).hexdigest()[:16]


def sync_docs(collection, source: str, docs: list[dict]) -> dict:
    """Привести документы источника к docs: новые/изменённые — upsert, пропавшие — удалить.

    Неизменённые документы не трогаются (эмбеддинги не пересчитываются).
    """
    if not docs:
        raise RuntimeError(f"{source}: источник вернул 0 документов, базу не трогаю")
    new = {d["id"]: d for d in docs}
    existing = {id_: (meta or {}).get("hash") for id_, meta in iter_metadatas(collection, {"source": source})}

    changed = [d for d in new.values() if existing.get(d["id"]) != doc_hash(d)]
    for i in range(0, len(changed), BATCH_SIZE):
        batch = changed[i : i + BATCH_SIZE]
        collection.upsert(
            ids=[d["id"] for d in batch],
            documents=[d["text"] for d in batch],
            metadatas=[{"title": d["title"], "chunk_id": 0, "source": source, "hash": doc_hash(d)}
                       for d in batch],
        )
    stale = [id_ for id_ in existing if id_ not in new]
    for i in range(0, len(stale), 1000):
        collection.delete(ids=stale[i : i + 1000])

    stats = {"docs": len(new), "updated": len(changed), "removed": len(stale)}
    log.info(f"{source}: {stats}")
    return stats


def sync(force_full: bool = False, only: tuple[str, ...] = SOURCES):
    """Обновить выбранные источники. Ошибка одного не мешает остальным; в конце — исключение."""
    state = load_state()
    collection = get_collection(create=True)
    before = collection.count()
    failed = []

    for source in only:
        started = datetime.now(timezone.utc)
        try:
            if source == "wiki":
                state["wiki"] = sync_wiki(collection, state.get("wiki", {}), started, force_full)
            elif source == "amber":
                stats = sync_docs(collection, "amber", src_amber.collect())
                state["amber"] = {"last_sync": started.isoformat(), "stats": stats}
            elif source == "paimon":
                os.makedirs(os.path.dirname(BANNERS_FILE) or ".", exist_ok=True)
                stats = sync_docs(collection, "paimon", src_paimon.collect(BANNERS_FILE))
                state["paimon"] = {"last_sync": started.isoformat(), "stats": stats}
            elif source == "tg":
                tg = get_collection(create=True, name=TG_COLLECTION)
                stats = sync_docs(tg, "tg", src_tg.collect())
                state["tg"] = {"last_sync": started.isoformat(), "stats": stats}
            save_state(state)
        except Exception:
            log.exception(f"{source}: синхронизация не удалась")
            failed.append(source)

    log.info(f"Синхронизация завершена: чанков {before} → {collection.count()}"
             + (f", ошибки: {failed}" if failed else ""))
    if failed:
        raise RuntimeError(f"Не удалось обновить: {', '.join(failed)}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Планировщик
# ═══════════════════════════════════════════════════════════════════════════════

def next_run(now: datetime) -> datetime:
    hh, mm = map(int, UPDATE_TIME.split(":"))
    run = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def daemon():
    log.info(f"Апдейтер запущен: синхронизация сейчас и ежедневно в {UPDATE_TIME} UTC")
    wake = datetime.now(timezone.utc)  # при старте сервиса — сразу

    while True:
        delay = (wake - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            log.info(f"Следующая синхронизация: {wake:%Y-%m-%d %H:%M} UTC")
            time.sleep(delay)
        try:
            sync()
            wake = next_run(datetime.now(timezone.utc))
        except Exception:
            log.exception(f"Синхронизация не удалась, повтор через {RETRY_MINUTES} мин")
            wake = datetime.now(timezone.utc) + timedelta(minutes=RETRY_MINUTES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="одна синхронизация и выход")
    parser.add_argument("--full", action="store_true", help="полная пересинхронизация вики и выход")
    parser.add_argument("--only", nargs="+", choices=SOURCES, default=list(SOURCES),
                        help="какие источники обновлять")
    args = parser.parse_args()

    if args.once or args.full:
        sync(force_full=args.full, only=tuple(args.only))
    else:
        daemon()


if __name__ == "__main__":
    sys.exit(main())
