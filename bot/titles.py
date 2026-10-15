"""Поиск в вопросе названий статей вики с учётом падежных окончаний."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable

from bot.config import MAX_TITLE_MATCHES, TITLES_TTL

log = logging.getLogger(__name__)

WORD_RE = re.compile(r"\w+")
ENDINGS = "аеиоуыэюяйь"

Entry = tuple[tuple[str, ...], str]  # (основы слов названия, название)


def stem(word: str) -> str:
    """Грубый стемминг: срезать до двух гласных/й/ь в конце (Лизы, Лизе → лиз)."""
    w = word.lower().replace("ё", "е")
    cut = 0
    while cut < 2 and len(w) - cut > 3 and w[len(w) - cut - 1] in ENDINGS:
        cut += 1
    return w[: len(w) - cut]


def stems(text: str) -> list[str]:
    return [stem(w) for w in WORD_RE.findall(text)]


def collection_titles(collection) -> set[str]:
    """Все названия статей из метаданных коллекции Chroma (постранично)."""
    titles: set[str] = set()
    offset, limit = 0, 5000
    while True:
        got = collection.get(include=["metadatas"], limit=limit, offset=offset)
        titles.update((m or {}).get("title", "") for m in got["metadatas"])
        if len(got["ids"]) < limit:
            return titles
        offset += limit


class TitleIndex:
    """Индекс названий: первая основа названия → полные основы. Перечитывается раз в ttl секунд."""

    def __init__(self, load_titles: Callable[[], Iterable[str]], ttl: float = TITLES_TTL,
                 max_matches: int = MAX_TITLE_MATCHES):
        self._load_titles = load_titles
        self._ttl = ttl
        self._max_matches = max_matches
        self._index: dict[str, list[Entry]] = {}
        self._loaded_at: float | None = None
        self._lock = threading.Lock()

    def _reload(self) -> None:
        titles = set(self._load_titles())
        index: dict[str, list[Entry]] = {}
        for title in titles:
            toks = tuple(stems(title))
            if toks and len(toks[0]) >= 3:
                index.setdefault(toks[0], []).append((toks, title))
        self._index = index
        self._loaded_at = time.monotonic()
        log.info("Индекс названий статей: %d", len(titles))

    def find(self, query: str) -> list[str]:
        """Названия статей, упомянутые в вопросе; длинные названия важнее коротких."""
        with self._lock:
            if self._loaded_at is None or time.monotonic() - self._loaded_at > self._ttl:
                self._reload()

        q = stems(query)
        candidates = []  # (число слов, длина, позиция, название)
        for i, tok in enumerate(q):
            # слово вопроса может быть длиннее основы названия на окончание
            for cut in range(0, 3):
                key = tok[: len(tok) - cut] if cut else tok
                for toks, title in self._index.get(key, ()):
                    if _match(q, i, toks):
                        candidates.append((len(toks), len(title), i, title))

        # «Сёгун Райдэн» вытесняет «Райдэн» на тех же словах
        used: set[int] = set()
        result: list[str] = []
        for n, _, i, title in sorted(candidates, reverse=True):
            span = set(range(i, i + n))
            if span & used or title in result:
                continue
            used |= span
            result.append(title)
            if len(result) == self._max_matches:
                break
        return result


def _match(q: list[str], i: int, toks: tuple[str, ...]) -> bool:
    if i + len(toks) > len(q):
        return False
    return all(
        qt.startswith(tt) and len(qt) - len(tt) <= 2
        for qt, tt in zip(q[i : i + len(toks)], toks, strict=True)
    )
