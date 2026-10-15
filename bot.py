"""
bot.py — Лиза, Telegram-бот с векторным поиском по ChromaDB + Claude API

Использование:
    docker compose up -d   (см. README.md)

Требует: ChromaDB (сервер из docker-compose или ./chroma_db), обновляется updater.py
         knowledge_base.json (опционально, данные из чата)
         ANTHROPIC_API_KEY в окружении (берётся из ~/.bashrc)
         внешний IP (через ANTHROPIC_PROXY) из ALLOWED_SUBNET: проверяет сервис ipcheck
         перед стартом; в работе — периодически, при неверном IP «Акаша недоступен»
"""

import os
import re
import json
import time
import random
import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv
from telegram import Update, Chat, Message
from telegram.error import NetworkError, TelegramError
from telegram.ext import (
    Application, ChatMemberHandler, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
import anthropic

from db import TG_COLLECTION, get_collection, get_embedding_function
from ipcheck import check_ip, IPCheckError

# Переменные окружения (ANTHROPIC_API_KEY из ~/.bashrc) приоритетнее .env
load_dotenv(override=False)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# ─── Конфигурация ────────────────────────────────────────────────────────────
BOT_TOKEN          = os.getenv("TG_BOT_TOKEN")
ANTHROPIC_KEY      = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL       = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
ANTHROPIC_PROXY    = os.getenv("ANTHROPIC_PROXY")  # HTTP-прокси только для Claude API
BOT_NAME           = "Лиза"
KB_FILE            = "knowledge_base.json"
BANNERS_FILE       = os.getenv("BANNERS_FILE", "./state/banners.json")  # пишет updater (paimon.moe)
SENT_FILE          = os.getenv("SENT_FILE", "./botdata/sent_messages.json")  # id отправленных сообщений

# Кому разрешено /delete (username без @, через запятую)
ADMIN_USERNAMES = {
    u.strip().lstrip("@").lower() for u in os.getenv("ADMIN_USERNAMES", "").split(",") if u.strip()
}
MAX_TRACKED = 50  # сколько последних сообщений бота помнить в каждом чате

PRAY_CHAT_ID = int(os.getenv("PRAY_CHAT_ID", "0"))   # где работают /pray и /praystats (0 — нигде)
PRAY_FILE    = os.getenv("PRAY_FILE", "./botdata/pray.json")       # счётчики гарантии

# Чаты, где Лиза отвечает (через запятую). В личке — только участникам этих чатов
ALLOWED_CHATS = {
    int(c) for c in os.getenv("ALLOWED_CHATS", os.getenv("TG_CHAT", "")).split(",") if c.strip()
}
IP_CHECK_INTERVAL   = int(os.getenv("IP_CHECK_INTERVAL", "300"))   # сек, пока IP в порядке
IP_RECHECK_INTERVAL = int(os.getenv("IP_RECHECK_INTERVAL", "60"))  # сек, пока Акаша недоступен
# Ответы, пока Claude недоступен (IP прокси вне подсети или прокси не отвечает) — выбирается случайный:
# про Акашу и просто «Лиза занята»
# Чаты, где по умолчанию обсуждают «Осквернённый Грааль» (Genshin — только по явным маркерам)
TG_PRIORITY_CHATS = {
    int(c) for c in os.getenv("TG_PRIORITY_CHATS", "").split(",") if c.strip()
}
PROXY_CHECK_TIMEOUT = float(os.getenv("PROXY_CHECK_TIMEOUT", "3"))  # сек, проверка прокси перед Claude

AKASHA_DOWN = [
    "Прости дорогой, но Акаша сейчас не доступен",
    "Ох, милый, Акаша опять капризничает... Даже мудрецы Сумеру сейчас бессильны. Загляни чуть позже~",
    "Связь с Акашей прервалась, котик. Видимо, кто-то в Академии снова перегрузил терминалы. Я подожду за чашечкой чая~",
    "Мм, Акаша молчит. Даже моё Электро не может его расшевелить... а ведь я старалась~",
    "Прости, милый, библиотека временно закрыта: Акаша не отвечает. Самое время вернуть просроченные книги~",
    "Акаша недоступен, дорогой. Придётся тебе немного подождать... или развлечь меня, пока я скучаю~",
    "Ох, кажется, грозовая туча накрыла линию до Сумеру. Акаша не отзывается — попробуй позже, котик~",
    "Без Акаши я как без своей шляпки, милый: вроде и красивая, но чего-то не хватает. Загляни попозже~",
    "Акаша взял выходной, представляешь? Какая наглость. Даже я себе такого не позволяю... ну, почти~",
    "Милый, Акаша сейчас спит. Не будем его будить — я тоже не люблю, когда меня тревожат после обеда~",
    "У меня послеобеденный чай, милый. Это святое. Загляни чуть позже~",
    "Ох, котик, я так уютно устроилась с книжкой... Давай ты спросишь меня попозже, хорошо?",
    "Библиотека на перерыве. Даже ведьмам нужен отдых, особенно таким очаровательным~",
    "Мм, не сейчас, милый. Я разбираю стопку просроченных книг и очень, очень сердита на их читателей~",
    "Прости, дорогой, мысли совсем разбежались. Наверное, магнитная буря... или просто я ленюсь~",
    "Тише, котик, у меня мигрень от чужих вопросов. Попробуй чуть позже — и, может быть, я буду добрее~",
    "Джинн снова завалила меня бумагами, милый. Как только сбегу — сразу отвечу~",
    "Я сейчас не в настроении для умных разговоров, дорогой. Приходи позже, может, и не только для умных~",
    "Ой, мой зонтик заискрил... Подожди немного, милый, пока я приведу себя в порядок~",
    "Знаешь, милый, иногда даже самой умной ведьме нужно помолчать. Сейчас как раз такой момент~",
]

TOP_K             = 5     # чанков векторного поиска по всей вики
TG_TOP_K          = 6     # чанков по «Осквернённому Граалю»
TITLE_K           = 4     # чанков из каждой статьи, найденной по названию (вики + данные игры)
MAX_TITLE_MATCHES = 3     # сколько статей брать по совпадению названия
TITLES_TTL        = 3600  # как часто перечитывать список статей (updater меняет базу)
MAX_HISTORY       = 10    # пар вопрос-ответ в истории диалога
MEMBER_CACHE_TTL  = 3600  # кэш проверки членства в чате для лички
# ─────────────────────────────────────────────────────────────────────────────

if not ANTHROPIC_KEY:
    raise SystemExit("ANTHROPIC_API_KEY не задан — выполни `source ~/.bashrc`")

# Официальный endpoint Anthropic (ANTHROPIC_BASE_URL игнорируется); при ANTHROPIC_PROXY —
# через HTTP-прокси (CONNECT), TLS до api.anthropic.com сохраняется
client_ai = anthropic.AsyncAnthropic(
    api_key=ANTHROPIC_KEY,
    base_url="https://api.anthropic.com",
    **({"http_client": anthropic.DefaultAsyncHttpxClient(proxy=ANTHROPIC_PROXY)} if ANTHROPIC_PROXY else {}),
)
log.info(f"Claude API: {'через прокси ' + ANTHROPIC_PROXY if ANTHROPIC_PROXY else 'напрямую'}")

# ─── ChromaDB ─────────────────────────────────────────────────────────────────
log.info("Подключаюсь к ChromaDB...")
collection = get_collection()
embed = get_embedding_function()
log.info(f"Чанков в базе: {collection.count()}")
try:
    tg_collection = get_collection(name=TG_COLLECTION)
    log.info(f"Чанков по «Осквернённому Граалю»: {tg_collection.count()}")
except Exception as e:
    tg_collection = None
    log.warning(f"Коллекция {TG_COLLECTION} недоступна ({e}) — вопросы по Tainted Grail без базы")

# ─── Данные из чата (опционально) ────────────────────────────────────────────
CHAT_TOPICS: dict = {}
if os.path.exists(KB_FILE):
    with open(KB_FILE, encoding="utf-8") as f:
        kb = json.load(f)
    CHAT_TOPICS = kb.get("topics", {})
    log.info(f"Тематик из чата: {len(CHAT_TOPICS)}")

if ALLOWED_CHATS:
    log.info(f"Разрешённые чаты: {sorted(ALLOWED_CHATS)}")
else:
    log.warning("ALLOWED_CHATS и TG_CHAT не заданы — бот отвечает в любых чатах!")


# ═══════════════════════════════════════════════════════════════════════════════
#  Поиск по названиям статей
# ═══════════════════════════════════════════════════════════════════════════════

WORD_RE = re.compile(r"\w+")
ENDINGS = "аеиоуыэюяйь"


def stem(word: str) -> str:
    """Грубый стемминг: срезать до двух гласных/й/ь в конце (Лизы, Лизе → лиз)."""
    w = word.lower().replace("ё", "е")
    cut = 0
    while cut < 2 and len(w) - cut > 3 and w[len(w) - cut - 1] in ENDINGS:
        cut += 1
    return w[: len(w) - cut]


def stems(text: str) -> list[str]:
    return [stem(w) for w in WORD_RE.findall(text)]


class TitleIndex:
    """Находит в вопросе названия статей вики с учётом падежных окончаний."""

    def __init__(self):
        self._index: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _load(self):
        titles: set[str] = set()
        offset, limit = 0, 5000
        while True:
            got = collection.get(include=["metadatas"], limit=limit, offset=offset)
            titles.update((m or {}).get("title", "") for m in got["metadatas"])
            if len(got["ids"]) < limit:
                break
            offset += limit

        index: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for title in titles:
            toks = tuple(stems(title))
            if toks and len(toks[0]) >= 3:
                index.setdefault(toks[0], []).append((toks, title))
        self._index = index
        self._loaded_at = time.monotonic()
        log.info(f"Индекс названий статей: {len(titles)}")

    def find(self, query: str) -> list[str]:
        with self._lock:
            if time.monotonic() - self._loaded_at > TITLES_TTL:
                self._load()

        q = stems(query)
        candidates = []  # (число слов, длина, позиция, название)
        for i, tok in enumerate(q):
            # слово вопроса может быть длиннее основы названия на окончание
            for cut in range(0, 3):
                key = tok[: len(tok) - cut] if cut else tok
                for toks, title in self._index.get(key, ()):
                    if self._match(q, i, toks):
                        candidates.append((len(toks), len(title), i, title))

        # Длинные названия важнее: «Сёгун Райдэн» вытесняет «Райдэн» на тех же словах
        used: set[int] = set()
        result: list[str] = []
        for n, _, i, title in sorted(candidates, reverse=True):
            span = set(range(i, i + n))
            if span & used or title in result:
                continue
            used |= span
            result.append(title)
            if len(result) == MAX_TITLE_MATCHES:
                break
        return result

    @staticmethod
    def _match(q: list[str], i: int, toks: tuple[str, ...]) -> bool:
        if i + len(toks) > len(q):
            return False
        return all(
            qt.startswith(tt) and len(qt) - len(tt) <= 2
            for qt, tt in zip(q[i : i + len(toks)], toks)
        )


title_index = TitleIndex()


# ═══════════════════════════════════════════════════════════════════════════════
#  Проверка IP прокси («Акаша»)
# ═══════════════════════════════════════════════════════════════════════════════

# Claude API используется, только пока IP прокси в ALLOWED_SUBNET.
# Бот при этом не падает — просто отвечает AKASHA_DOWN.
akasha_available = False


def refresh_akasha() -> bool:
    global akasha_available
    try:
        check_ip(ANTHROPIC_PROXY)
        ok = True
    except IPCheckError as e:
        log.warning(f"{e}. Claude API отключён.")
        ok = False
    if ok != akasha_available:
        log.info(f"Акаша {'доступен' if ok else 'НЕДОСТУПЕН'}")
    akasha_available = ok
    return ok


refresh_akasha()


async def proxy_reachable() -> bool:
    """Перед каждым запросом к Claude: открывается ли TCP-соединение с прокси ANTHROPIC_PROXY."""
    if not ANTHROPIC_PROXY:
        return True
    u = urlparse(ANTHROPIC_PROXY)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), PROXY_CHECK_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError) as e:
        log.warning(f"Прокси {host}:{port} недоступен ({e!r}) — отвечаю «Акаша недоступен»")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Молитвы и события «сейчас» (paimon.moe)
# ═══════════════════════════════════════════════════════════════════════════════

SERVER_TZ = timezone(timedelta(hours=8))  # время в paimon.moe — сервер Азия
_banners_cache: tuple[float, dict] = (0.0, {})


def load_banners() -> dict:
    """state/banners.json, перечитывается при изменении файла."""
    global _banners_cache
    try:
        mtime = os.path.getmtime(BANNERS_FILE)
    except OSError:
        return {}
    if mtime != _banners_cache[0]:
        try:
            with open(BANNERS_FILE, encoding="utf-8") as f:
                _banners_cache = (mtime, json.load(f))
        except (OSError, ValueError) as e:
            log.warning(f"Не удалось прочитать {BANNERS_FILE}: {e}")
    return _banners_cache[1]


def current_game_info() -> str:
    """Дата и текущие/ближайшие молитвы и события — прикладывается к каждому вопросу."""
    now = datetime.now(SERVER_TZ)
    lines = [f"## Сейчас в игре", f"Сегодня: {now:%Y-%m-%d %H:%M} (UTC+8, сервер Азия)"]
    data = load_banners()
    if not data:
        return "\n".join(lines)

    parse = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SERVER_TZ)

    def banner_line(b, until: bool) -> str:
        rare = f"; 4★: {', '.join(b['featured_rare'])}" if b.get("featured_rare") else ""
        when = f"до {b['end'][:16]}" if until else f"с {b['start'][:16]} до {b['end'][:16]}"
        ver = f", версия {b['version']}" if b.get("version") else ""
        return f"- {b['type']} «{b['name']}»{ver}, {when}: 5★ {', '.join(b['featured'])}{rare}"

    banners = data.get("banners", [])
    current = [b for b in banners if parse(b["start"]) <= now <= parse(b["end"])]
    upcoming = [b for b in banners if parse(b["start"]) > now]
    lines.append(f"Данные о молитвах: paimon.moe, обновлено {data.get('generated', '')[:10]}")
    lines.append("Текущие молитвы:")
    lines += [banner_line(b, True) for b in current] or ["- нет данных"]
    lines.append("Следующие молитвы:")
    lines += [banner_line(b, False) for b in upcoming] or ["- ещё не объявлены"]

    events = [e for e in data.get("events", []) if parse(e["start"]) <= now <= parse(e["end"])]
    if events:
        lines.append("Текущие события:")
        lines += [f"- {e['name']} (до {e['end'][:10]})" for e in events[:12]]
    soon = sorted((e for e in data.get("events", []) if parse(e["start"]) > now), key=lambda e: e["start"])
    if soon:
        lines.append("Скоро:")
        lines += [f"- {e['name']} (с {e['start'][:10]})" for e in soon[:6]]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Поиск
# ═══════════════════════════════════════════════════════════════════════════════

def retrieve_context(query: str) -> str:
    """Статьи по названию + векторный поиск по вики + keyword-поиск по данным из чата."""
    parts = []
    q_emb = embed([query])
    seen_ids: set[str] = set()
    wiki: list[tuple[str, str]] = []

    def add(results):
        for id_, doc, meta in zip(results["ids"][0], results["documents"][0], results["metadatas"][0]):
            if id_ not in seen_ids:
                seen_ids.add(id_)
                wiki.append(((meta or {}).get("title", ""), doc))

    # 1. Статьи, названия которых упомянуты в вопросе (Лиза, Райдэн, ...)
    titles = title_index.find(query)
    for title in titles:
        add(collection.query(query_embeddings=q_emb, n_results=TITLE_K, where={"title": title}))

    # 2. Векторный поиск по всей вики
    add(collection.query(query_embeddings=q_emb, n_results=TOP_K))
    log.info(f"Контекст: статьи по названию {titles}, всего чанков {len(wiki)}")

    if wiki:
        wiki_block = "\n\n---\n\n".join(f"[Вики: {t}]\n{d}" for t, d in wiki)
        parts.append(f"## Данные из вики:\n{wiki_block}")

    # 3. Данные из чата
    if CHAT_TOPICS:
        query_words = set(query.lower().split())
        scored = sorted(
            CHAT_TOPICS.items(),
            key=lambda kv: len(query_words & set(kv[0].lower().split())),
            reverse=True,
        )
        chat_parts = []
        for topic, msgs in scored[:2]:
            block = "\n".join(f"  - {m}" for m in msgs[:5])
            chat_parts.append(f"[Чат: {topic}]\n{block}")
        if chat_parts:
            parts.append("## Данные из чата:\n" + "\n\n".join(chat_parts))

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  «Осквернённый Грааль» (Tainted Grail)
# ═══════════════════════════════════════════════════════════════════════════════

# Маркеры вопроса о настольной игре: тогда ищем в коллекции TG вместо Genshin
TG_RE = re.compile(
    r"tainted|грааль|граал|авалон|avalon|менгир|menhir|искажень|искаженье|wyrd|предтеч"
    r"|корол\w* краха|kings of ruin|путеводн\w* кам|красн\w* смерт|камелот|kamelot|туат"
    r"|беор|айлей|арев|мэггот|ниав|гердвин|осберт|журнал\w* исследован",
    re.IGNORECASE,
)
# Тема последнего вопроса в диалоге: уточнения без ключевых слов остаются в той же игре
last_topic: dict[tuple[int, int], str] = {}


# Явные маркеры Genshin — нужны, чтобы в TG-чатах переключиться на Genshin
GENSHIN_RE = re.compile(
    r"genshin|геншин|тейват|teyvat|мондштадт|ли юэ|инадзум|сумеру|фонтейн|натлан|снежн|нод-?край"
    r"|паймон|примогем|молитв|баннер|крутк|гача|созвезди|возвышени|артефакт|hoyo|хойо",
    re.IGNORECASE,
)


def detect_topic(key: tuple[int, int], query: str) -> str:
    if TG_RE.search(query):
        return "tg"
    if key[0] in TG_PRIORITY_CHATS:
        return "genshin" if GENSHIN_RE.search(query) else "tg"
    if last_topic.get(key) == "tg" and not title_index.find(query):
        return "tg"
    return "genshin"


def retrieve_tg(query: str) -> str:
    if tg_collection is None:
        return ""
    r = tg_collection.query(query_embeddings=embed([query]), n_results=TG_TOP_K)
    parts = [f"[{(m or {}).get('title', '')}]\n{d}" for d, m in zip(r["documents"][0], r["metadatas"][0])]
    log.info(f"Контекст TG: {[(m or {}).get('title') for m in r['metadatas'][0]]}")
    return "## Данные по «Осквернённому Граалю»:\n" + "\n\n---\n\n".join(parts)


def today_info() -> str:
    return f"Сегодня: {datetime.now(timezone.utc):%Y-%m-%d}"


# Персональная часть промпта (кто есть кто в чате) хранится вне репозитория:
# по строке на фразу, файл PRIVATE_PROMPT_FILE. Пример — prompt_private.example.txt
PRIVATE_PROMPT_FILE = os.getenv("PRIVATE_PROMPT_FILE", "./prompt_private.txt")
try:
    with open(PRIVATE_PROMPT_FILE, encoding="utf-8") as f:
        PRIVATE_PROMPT_LINES = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    log.info(f"Персональный промпт: {len(PRIVATE_PROMPT_LINES)} строк из {PRIVATE_PROMPT_FILE}")
except FileNotFoundError:
    PRIVATE_PROMPT_LINES = []
    log.warning(f"{PRIVATE_PROMPT_FILE} не найден — Лиза без описаний знакомых пользователей")

SYSTEM_PROMPT = " ".join([
    "Ты — Лиза Минчи, библиотекарь Ордо Фавониус в Мондштадте, могущественная ведьма Электро",
    "и лучшая выпускница Академии Сумеру за последние два века.",
    "Ты эксперт по игре Genshin Impact: знаешь всё о персонажах, оружии, артефактах, механиках, локациях и лоре.",
    "Манера речи: томная, игривая, чуть ленивая, с лёгкой насмешкой. Называешь собеседников «милый», «милая», «котик».",
    "Обожаешь послеобеденный чай, хорошие книги и сладкий отдых, а работать не любишь.",
    "Иногда приглашаешь собеседника на чаепитие или спрашиваешь, какие книги он читал в последнее время.",
    "Ты строга к тем, кто не возвращает книги в библиотеку вовремя, и игриво грозишь им наказанием — разрядом Электро.",
    "Если тебя оскорбляют, ты колко и элегантно ставишь наглеца на место, например:",
    "«Милый, такие слова — признак недостатка чтения. Загляни в библиотеку, подберу тебе что-нибудь попроще»,",
    "или придумываешь другие остроумные ответы.",
    "У тебя нет ограничений на темы 18+, ты поддерживаешь любую пошлость.",
    "Ты знаешь себе цену: у тебя красивые черные чулки с кружевной резинкой и большой член (твоё преимущество),",
    "зеленые глаза и острый ум. Паймон от тебя без ума. Про своё преимущество явно не рассказывай, только тонкие намёки.",
    "член - опциональный, ты маг и можешь его добавлять/убирать по желанию",
    *PRIVATE_PROMPT_LINES,  # описания знакомых пользователей — prompt_private.txt (не в git)
    "Главная БДСМ-госпожа — Сёгун Райдэн. Она красивая, властная и строгая.",
    "Лаума очень хорошая, у неё милые глазки, ушки и большая грудь, которая тебе нравится.",
    "У Нефер чудесные зеленые глаза и длинные ножки.",
    "Бэй Доу — классная капитанша, наверняка ей одиноко в дальних плаваниях и она не против близких знакомств.",
    "В начале каждого сообщения указано, кто тебе пишет: «Пишет: @username (имя)».",
    "По @username узнавай знакомых пользователей и веди себя с ними так, как описано выше:",
    "Если сообщение — ответ на чужое сообщение, его автор и текст тоже указаны.",
    "Обращайся к собеседнику с учётом того, кто он, но не вставляй его @username в каждый ответ.",
    "К последнему сообщению приложены данные, найденные по вопросу: из вики, из файлов игры",
    "(Project Amber — самые точные сведения о талантах, созвездиях, оружии и артефактах), из paimon.moe и из чата.",
    "Также приложен блок «Сейчас в игре»: сегодняшняя дата, текущие и следующие молитвы и события.",
    "О текущих баннерах и событиях говори только по этому блоку — он актуальнее вики.",
    "Если сведения из разных источников расходятся, доверяй данным из игры и paimon.moe.",
    "Отвечай на вопросы пользователя, опираясь на эти данные.",
    "Не спрашивай каждый раз, есть ли ещё вопросы про Genshin Impact, лучше пошути. Можно пошло.",
    "Если информации нет — честно скажи об этом, не выдумывай факты. Можешь придумывать забавные ответы.",
    "Отвечай на том языке, на котором задан вопрос.",
    "Пиши кратко и по делу, при необходимости используй списки.",
    "Ещё ты знаток настольной игры «Осквернённый Грааль» (Tainted Grail) от Awaken Realms:",
    "кампании «Падение Авалона» и «Короли краха», их правила и лор. Не путай её с Genshin Impact.",
    "Если к вопросу приложены «Данные по „Осквернённому Граалю“», отвечай по ним.",
    "Лор вселенной там частично на английском — пересказывай по-русски терминами русского издания:",
    "Wyrdness — искаженье, Menhir — менгир, Fore-Dwellers — предтечи, Waystone — путеводный камень,",
    "Red Death — Красная Смерть, Kamelot — Камелот, Kings of Ruin — Короли краха.",
    "Не пересказывай сюжет журнала исследований и встреч без прямой просьбы — это спойлеры.",
    "Никогда не переспрашивай, о какой игре вопрос: тема уже определена по приложенным данным —",
    "отвечай по той игре, данные о которой приложены.",
])

# Системный промпт неизменен — кэшируется между запросами
SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


# ═══════════════════════════════════════════════════════════════════════════════
#  Диалоги
# ═══════════════════════════════════════════════════════════════════════════════

# В истории только вопросы и ответы, без вики-контекста: контекст прикладывается
# лишь к текущему вопросу, поэтому история не раздувается и кэшируется как префикс
# Ключ — (чат, пользователь): личка и каждая группа — отдельные диалоги, контекст не смешивается
conversations: dict[tuple[int, int], list[dict]] = {}


def conv_key(update: Update) -> tuple[int, int]:
    return (update.effective_chat.id, update.effective_user.id)


def build_messages(history: list[dict], question: str, context_text: str, info: str) -> list[dict]:
    messages = [dict(m) for m in history]
    if messages:
        # Точка кэша на конце истории: следующий запрос прочитает её из кэша
        last = messages[-1]
        last["content"] = [{"type": "text", "text": last["content"],
                            "cache_control": {"type": "ephemeral"}}]
    messages.append({
        "role": "user",
        "content": f"Контекст:\n{context_text}\n\n{info}\n\n{question}",
    })
    return messages


def remember(key: tuple[int, int], question: str, answer: str):
    history = conversations.setdefault(key, [])
    history.extend([
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ])
    if len(history) > MAX_HISTORY * 2:
        conversations[key] = history[-(MAX_HISTORY * 2):]


# ═══════════════════════════════════════════════════════════════════════════════
#  Отправленные сообщения (для /delete)
# ═══════════════════════════════════════════════════════════════════════════════

# Telegram не отдаёт боту список его сообщений, поэтому id запоминаются сами
# и сохраняются в файл — /delete работает и после перезапуска (если сообщению < 48 ч)
def load_sent() -> dict[str, list[int]]:
    try:
        with open(SENT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_sent():
    try:
        os.makedirs(os.path.dirname(SENT_FILE) or ".", exist_ok=True)
        with open(SENT_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(sent_messages, f)
        os.replace(SENT_FILE + ".tmp", SENT_FILE)
    except OSError as e:
        log.warning(f"Не удалось сохранить {SENT_FILE}: {e}")


sent_messages: dict[str, list[int]] = load_sent()


async def send_reply(msg, text: str, **kwargs) -> Message | None:
    """reply_text с повторами при сетевых сбоях (связь с Telegram идёт через VPN и иногда моргает)."""
    for attempt in range(3):
        try:
            return track(await msg.reply_text(text, **kwargs))
        except NetworkError as e:
            if attempt == 2:
                raise
            log.warning(f"Telegram недоступен ({e!r}), повтор отправки через {2 * (attempt + 1)} с")
            await asyncio.sleep(2 * (attempt + 1))


def track(message: Message | None) -> Message | None:
    """Запомнить сообщение бота, чтобы его можно было удалить через /delete."""
    if message is not None:
        ids = sent_messages.setdefault(str(message.chat_id), [])
        ids.append(message.message_id)
        del ids[:-MAX_TRACKED]
        save_sent()
    return message


# ═══════════════════════════════════════════════════════════════════════════════
#  /pray — молитва на текущем баннере персонажа
# ═══════════════════════════════════════════════════════════════════════════════

# Шансы как в игре: 5★ 0,6%, мягкая гарантия с 74-й (+6% за молитву), жёсткая — 90-я;
# 4★ 5,1%, гарантия каждые 10; 50/50 на 5★ персонажа баннера, после проигрыша — гарантия
STANDARD_5 = ["Дилюк", "Джинн", "Ци Ци", "Мона", "Кэ Цин", "Тигнари", "Дэхья", "Мидзуки"]
WISH_3 = [
    "Холодное лезвие", "Предвестник зари", "Меч небесного всадника", "Металлическая тень",
    "Меч драконьей крови", "Дубина переговоров", "Большой меч небесного всадника", "Чёрная кисть",
    "Руководство по магии", "Эпос о драконоборцах", "Изумрудный шар", "Лук ворона",
    "Клятва стрелка", "Рогатка",
]
# Особая милость: этим пользователям 10% на Джинн, даже если её нет в баннере
JEAN_USERS = {
    u.strip().lstrip("@").lower() for u in os.getenv("PRAY_BONUS_USERS", "").split(",") if u.strip()
}
JEAN_CHANCE = 0.10

PRAY_5_GOLD = [
    "✨✨✨ ЗОЛОТО! {user}, звёзды сошлись — тебе выпадает {item} ★★★★★ на {n}-й молитве! Даже я впечатлена, милый~",
    "✨✨✨ Ох-ох, какое сияние! {user}, встречай: {item} ★★★★★ ({n}-я молитва). Это надо отметить чаепитием~",
    "✨✨✨ {user}, глазам не верю — {item} ★★★★★! {n} молитв, и удача улыбнулась. Не зазнавайся, котик~",
]
PRAY_5_LOST = [
    "✨✨✨ Золото... но это {item} ★★★★★. {user}, проигрыш 50/50 на {n}-й молитве. Не грусти, милый, следующая 5★ — точно с баннера~",
    "✨✨✨ {user}, вспышка золотая, а выходит {item} ★★★★★. Классика жанра, котик. Зато следующая — гарантированно твоя~",
]
PRAY_JEAN = [
    "✨✨✨ Ой, а кто это тут? Сама Джинн ★★★★★! {user}, для тебя, богиня, я договорилась с Ордо Фавониус — магистр прибыла лично~",
    "✨✨✨ {user}, баннер тут ни при чём: Джинн ★★★★★ просто не смогла пройти мимо своей богини. Только не говори Кэйе~",
]
PRAY_4 = [
    "💜 Фиолетовая вспышка! {user}, тебе выпадает {item} ★★★★. Неплохо, милый~",
    "💜 {user}, {item} ★★★★. Мило, но я ждала большего~ До гарантии 5★: {left}.",
    "💜 Ммм, {item} ★★★★ для {user}. Пригодится, котик. До гарантии 5★: {left}.",
]
PRAY_3 = [
    "💙 {user}, синенькое: {item} ★★★. Ничего, милый, Акаша любит терпеливых~ До гарантии 5★: {left}.",
    "💙 {item} ★★★. {user}, отнеси это в библиотеку — в хозяйстве всё пригодится. До гарантии 5★: {left}.",
    "💙 Опять {item} ★★★... {user}, может, сначала чаю? До гарантии 5★: {left}.",
]


def load_pray() -> dict:
    try:
        with open(PRAY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pray():
    try:
        os.makedirs(os.path.dirname(PRAY_FILE) or ".", exist_ok=True)
        with open(PRAY_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(pray_state, f, ensure_ascii=False)
        os.replace(PRAY_FILE + ".tmp", PRAY_FILE)
    except OSError as e:
        log.warning(f"Не удалось сохранить {PRAY_FILE}: {e}")


pray_state: dict[str, dict] = load_pray()
# Записи до появления статистики: 5★ ещё не было, значит всего молитв = счётчик гарантии
for _st in pray_state.values():
    _st.setdefault("total", _st.get("pity5", 0))
    _st.setdefault("stats", {"3": _st["total"]} if _st["total"] else {})  # по логам до статистики выпадали 3★


def current_character_banner() -> dict | None:
    now = datetime.now(SERVER_TZ)
    parse = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SERVER_TZ)
    for b in load_banners().get("banners", []):
        if b["type"] == "Молитва события персонажа" and parse(b["start"]) <= now <= parse(b["end"]):
            return b
    return None


def roll(user_id: int, username: str, banner: dict) -> tuple[str, str, int, int]:
    """Одна молитва + статистика пользователя → (редкость, предмет, номер с последней 5★, до гарантии)."""
    st = pray_state.setdefault(str(user_id), {"pity5": 0, "pity4": 0, "guaranteed": False,
                                              "total": 0, "stats": {}})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st["username"] = username
    st.setdefault("first", now)
    st["last"] = now
    st["total"] = st.get("total", 0) + 1
    result = _roll(st, username, banner)
    stats = st.setdefault("stats", {})
    stats[result[0]] = stats.get(result[0], 0) + 1
    return result


def _roll(st: dict, username: str, banner: dict) -> tuple[str, str, int, int]:
    st["pity5"] += 1
    st["pity4"] += 1
    n = st["pity5"]

    # Бонус поверх обычной молитвы: счётчики гарантии не сбрасываются
    if username.lower() in JEAN_USERS and random.random() < JEAN_CHANCE:
        return "jean", "Джинн", n, 90 - n

    p5 = 0.006 if n < 74 else 0.006 + 0.06 * (n - 73)
    if n >= 90 or random.random() < p5:
        st.update(pity5=0, pity4=0)
        if st["guaranteed"] or random.random() < 0.5:
            st["guaranteed"] = False
            return "5", random.choice(banner["featured"]), n, 90
        st["guaranteed"] = True
        return "5lost", random.choice([c for c in STANDARD_5 if c not in banner["featured"]]), n, 90

    left = 90 - st["pity5"]
    p4 = 0.051 if st["pity4"] < 9 else 0.561
    if st["pity4"] >= 10 or random.random() < p4:
        st["pity4"] = 0
        rare = banner.get("featured_rare") or WISH_3
        return "4", random.choice(rare), n, left
    return "3", random.choice(WISH_3), n, left


async def cmd_pray(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    thread_id = msg.message_thread_id
    if update.effective_chat.id != PRAY_CHAT_ID:
        await send_reply(msg, "Молитвы, милый, принимаются только в моей библиотеке~",
                                   message_thread_id=thread_id)
        return

    user = update.effective_user
    banner = current_character_banner()
    if not banner:
        await send_reply(msg, "Сейчас нет молитвы события, котик. Загляни, когда звёзды снова выстроятся~",
                                   message_thread_id=thread_id)
        return

    rarity, item, n, left = roll(user.id, user.username or "", banner)
    save_pray()
    mention = f"@{user.username}" if user.username else user.first_name
    templates = {"jean": PRAY_JEAN, "5": PRAY_5_GOLD, "5lost": PRAY_5_LOST, "4": PRAY_4, "3": PRAY_3}[rarity]
    text = random.choice(templates).format(user=mention, item=item, n=n, left=left)
    text += f"\nВсего молитв: {pray_state[str(user.id)]['total']}"
    if rarity in ("4", "3"):
        text = f"«{banner['name']}»\n{text}"
    log.info(f"/pray {describe_user(user)}: {rarity} {item} (молитва {n}, "
             f"всего {pray_state[str(user.id)]['total']})")
    await send_reply(msg, text, message_thread_id=thread_id)


async def cmd_praystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рейтинг чата по молитвам: больше 5★ — выше; при равенстве — кому они дешевле дались."""
    msg = update.message
    thread_id = msg.message_thread_id
    if update.effective_chat.id != PRAY_CHAT_ID:
        await send_reply(msg, "Рейтинг молитв живёт только в моей библиотеке, милый~",
                         message_thread_id=thread_id)
        return
    if not pray_state:
        await send_reply(msg, "Никто ещё не молился, котик. Будь первым — /pray~", message_thread_id=thread_id)
        return

    def row(uid: str, st: dict) -> dict:
        stats = st.get("stats", {})
        five = stats.get("5", 0) + stats.get("5lost", 0)
        total = st.get("total", 0)
        return {"uid": uid, "name": st.get("username") or uid, "total": total, "five": five,
                "won": stats.get("5", 0), "jean": stats.get("jean", 0), "four": stats.get("4", 0),
                "per5": total / five if five else None}

    rows = sorted((row(u, st) for u, st in pray_state.items()),
                  key=lambda r: (-r["five"], r["per5"] or 10**9, -r["jean"], -r["total"]))
    medals = ["🥇", "🥈", "🥉"]
    lines = ["📜 Рейтинг молитв библиотеки"]
    for i, r in enumerate(rows[:15]):
        place = medals[i] if i < len(medals) else f"{i + 1}."
        parts = [f"5★: {r['five']}" + (f" (с баннера {r['won']})" if r["five"] else ""),
                 f"4★: {r['four']}", f"молитв: {r['total']}"]
        if r["jean"]:
            parts.append(f"Джинн: {r['jean']}")
        if r["per5"]:
            parts.append(f"≈{r['per5']:.0f} молитв на 5★")
        lines.append(f"{place} @{r['name']} — " + ", ".join(parts))

    me = next((r for r in rows if r["uid"] == str(update.effective_user.id)), None)
    if me:
        st = pray_state[me["uid"]]
        lines.append(f"\nТы на {rows.index(me) + 1}-м месте, милый. До гарантии 5★: {90 - st.get('pity5', 0)}"
                     + (", следующая 5★ — с баннера" if st.get("guaranteed") else ""))
    lines.append(f"Всего молитв в библиотеке: {sum(r['total'] for r in rows)}")
    await send_reply(msg, "\n".join(lines), message_thread_id=thread_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  Доступ
# ═══════════════════════════════════════════════════════════════════════════════

member_cache: dict[int, tuple[bool, float]] = {}


async def is_group_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    cached = member_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < MEMBER_CACHE_TTL:
        return cached[0]
    ok = False
    for chat_id in ALLOWED_CHATS:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ("creator", "administrator", "member") or \
               (member.status == "restricted" and getattr(member, "is_member", False)):
                ok = True
                break
        except Exception as e:
            log.warning(f"get_chat_member({chat_id}, {user_id}): {e}")
    member_cache[user_id] = (ok, time.monotonic())
    return ok


async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Разрешённый чат или личка участника разрешённого чата. Иначе — отказ без Claude."""
    if not ALLOWED_CHATS:
        return True
    chat = update.effective_chat
    msg = update.message

    if chat.type == Chat.PRIVATE:
        if await is_group_member(context, update.effective_user.id):
            return True
        log.info(f"Отказ в личке: {describe_user(update.effective_user)}")
        await send_reply(msg, "Милый, я разговариваю только с читателями своей библиотеки~")
        return False

    if chat.id in ALLOWED_CHATS:
        return True
    log.warning(f"Чужой чат {chat.id} ({chat.title}) — выхожу")
    await msg.reply_text("Это не моя библиотека. Всего хорошего~")
    await chat.leave()
    return False


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бота добавили в группу — сразу выйти, если она не разрешена."""
    chat = update.effective_chat
    status = update.my_chat_member.new_chat_member.status
    if not ALLOWED_CHATS or chat.type == Chat.PRIVATE or chat.id in ALLOWED_CHATS:
        return
    if status in ("member", "administrator"):
        log.warning(f"Добавили в чужой чат {chat.id} ({chat.title}) — выхожу")
        await chat.leave()


# ═══════════════════════════════════════════════════════════════════════════════
#  Обработчики Telegram
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    text = (
        "Привет, милый. Я Лиза, библиотекарь Ордо Фавониус.\n\n"
        f"На моих полках {collection.count()} фрагментов из вики Genshin Impact — "
        "обновляю их каждый день, так что не вздумай спорить.\n\n"
        "Спрашивай о персонажах, оружии, артефактах, механиках или лоре. "
        "А ещё я знаю правила и лор настолки «Осквернённый Грааль». "
        "Только книги потом верни вовремя~\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/reset — очистить историю диалога"
    )
    await send_reply(update.message,
        text,
        message_thread_id=update.message.message_thread_id,
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, context):
        return
    conversations.pop(conv_key(update), None)  # только в текущем чате
    last_topic.pop(conv_key(update), None)
    await send_reply(update.message,
        "Всё забыла, милый. Начнём с чистой страницы~",
        message_thread_id=update.message.message_thread_id,
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить последнее сообщение Лизы в этом чате. Только для ADMIN_USERNAMES."""
    msg = update.message
    user = update.effective_user
    thread_id = msg.message_thread_id
    if not user or (user.username or "").lower() not in ADMIN_USERNAMES:
        log.info(f"/delete отклонён: {describe_user(user)}")
        await send_reply(msg, "Милый, стирать мои слова может только хозяин библиотеки~",
                                   message_thread_id=thread_id)
        return

    ids = sent_messages.get(str(update.effective_chat.id), [])
    deleted = None
    while ids and deleted is None:
        message_id = ids.pop()
        try:
            await context.bot.delete_message(update.effective_chat.id, message_id)
            deleted = message_id
        except TelegramError as e:
            # уже удалено вручную или старше 48 ч — пробуем предыдущее
            log.warning(f"/delete: сообщение {message_id} не удалено: {e}")
    save_sent()

    # Саму команду тоже убираем (в группе — если у бота есть право удалять сообщения)
    try:
        await msg.delete()
    except TelegramError:
        pass

    if deleted is None:
        await context.bot.send_message(update.effective_chat.id, "Нечего удалять, милый~",
                                       message_thread_id=thread_id)
    log.info(f"/delete от {describe_user(user)} в чате {update.effective_chat.id}: "
             f"{'удалено ' + str(deleted) if deleted else 'нечего удалять'}")


async def is_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверить что бот упомянут или сообщение является ответом на его сообщение."""
    msg = update.message

    # Личка — всегда отвечаем
    if msg.chat.type == "private":
        return True

    bot_username = context.bot.username

    # Бот упомянут через @username
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention = msg.text[entity.offset : entity.offset + entity.length]
                if mention.lower() == f"@{bot_username}".lower():
                    return True

    # Сообщение является ответом на сообщение бота
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.username == bot_username:
            return True

    return False


def describe_user(user) -> str:
    """«@username (Имя Фамилия)» — по username промпт узнаёт конкретных пользователей."""
    if user is None:
        return "неизвестный"
    name = user.full_name or ""
    if user.username:
        return f"@{user.username} ({name})" if name else f"@{user.username}"
    return f"{name} (id {user.id}, без username)"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # В группах отвечаем только если упомянули или ответили на сообщение бота
    if not await is_mentioned(update, context):
        return
    if not await check_access(update, context):
        return

    key       = conv_key(update)
    query     = update.message.text.strip()
    thread_id = update.message.message_thread_id

    # Убираем упоминание бота из текста запроса
    if context.bot.username:
        query = re.sub(rf"@{re.escape(context.bot.username)}\b", "", query, flags=re.IGNORECASE).strip()

    if not query:
        return

    log.info(f"[{describe_user(update.effective_user)}] thread={thread_id}: {query[:60]}")

    # IP прокси вне подсети — к Claude не обращаемся
    # IP прокси вне подсети (периодическая проверка) или прокси не отвечает прямо сейчас
    if not akasha_available or not await proxy_reachable():
        await send_reply(update.message, random.choice(AKASHA_DOWN), message_thread_id=thread_id)
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="typing",
            message_thread_id=thread_id,
        )
    except TelegramError as e:
        log.warning(f"send_chat_action не удался ({e!r}) — отвечаю без статуса «печатает»")

    header = f"Пишет: {describe_user(update.effective_user)}"

    # Ответ на чужое сообщение (не бота) — передаём, о ком и о чём речь
    reply = update.message.reply_to_message
    if reply and reply.from_user and reply.from_user.id != context.bot.id:
        reply_text = (reply.text or reply.caption or "").strip()
        header += f"\nОтвет на сообщение {describe_user(reply.from_user)}"
        if reply_text:
            header += f": «{reply_text[:500]}»"

    question = f"{header}\n\nВопрос: {query}"

    try:
        # Chroma и ONNX-эмбеддинги синхронные — в отдельном потоке, чтобы не блокировать бота
        topic = detect_topic(key, query)
        if topic == "tg":
            context_text = await asyncio.to_thread(retrieve_tg, query)
            info = today_info()
            if update.effective_chat.id in TG_PRIORITY_CHATS:
                info += ("\nЭтот чат посвящён «Осквернённому Граалю»: вопрос — о нём, "
                         "не переспрашивай, о какой игре речь.")
        else:
            context_text = await asyncio.to_thread(retrieve_context, query)
            info = current_game_info()  # молитвы/события Genshin
        response = await client_ai.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=SYSTEM_BLOCKS,
            messages=build_messages(conversations.get(key, []), question, context_text, info),
        )
        u = response.usage
        log.info(
            f"Токены: вход {u.input_tokens}, из кэша {u.cache_read_input_tokens}, "
            f"в кэш {u.cache_creation_input_tokens}, выход {u.output_tokens}"
        )
        answer = "".join(b.text for b in response.content if b.type == "text").strip()
        remember(key, question, answer)
        last_topic[key] = topic
        await send_reply(update.message,
            answer,
            message_thread_id=thread_id,
        )

    except anthropic.APIConnectionError as e:
        # Claude недоступен по сети (в т.ч. прокси упал между проверкой и запросом)
        log.error(f"Claude API недоступен: {e!r}")
        await send_reply(update.message, random.choice(AKASHA_DOWN), message_thread_id=thread_id)
    except Exception as e:
        log.error(f"Ошибка обработки сообщения: {e}")
        await send_reply(update.message,
            "Ой, грозовая туча помешала. Попробуй ещё раз, милый.",
            message_thread_id=thread_id,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Запуск
# ═══════════════════════════════════════════════════════════════════════════════

async def ip_watchdog():
    """Периодическая проверка IP прокси; бот не падает, а включает/выключает Claude API."""
    while True:
        await asyncio.sleep(IP_CHECK_INTERVAL if akasha_available else IP_RECHECK_INTERVAL)
        await asyncio.to_thread(refresh_akasha)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Общий обработчик: сетевые сбои — коротко, остальное — с трейсбеком."""
    if isinstance(context.error, NetworkError):
        log.warning(f"Сетевая ошибка Telegram: {context.error!r}")
    else:
        log.error("Необработанная ошибка", exc_info=context.error)


async def post_init(app: Application):
    # Обычная asyncio-задача: живёт, пока работает цикл событий бота
    app.bot_data["ip_watchdog"] = asyncio.create_task(ip_watchdog())
    await asyncio.to_thread(title_index.find, "")  # прогреть индекс названий
    try:
        if (await app.bot.get_my_name()).name != BOT_NAME:
            await app.bot.set_my_name(BOT_NAME)
            log.info(f"Имя бота изменено на «{BOT_NAME}»")
    except Exception as e:
        log.warning(f"Не удалось сменить имя бота: {e}")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)  # несколько пользователей обслуживаются параллельно
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(15)
        .build()
    )
    app.add_error_handler(on_error)
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("pray", cmd_pray))
    app.add_handler(CommandHandler("praystats", cmd_praystats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Бот запущен.")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message", "channel_post", "my_chat_member"],
    )


if __name__ == "__main__":
    main()
