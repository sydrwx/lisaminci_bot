"""Поиск контекста для вопроса: вики и данные игры, «Осквернённый Грааль», данные из чата.

Chroma и ONNX-эмбеддинги синхронные — методы вызываются через asyncio.to_thread.
"""

from __future__ import annotations

import json
import logging
import os

from bot.config import TG_TOP_K, TITLE_K, TOP_K
from bot.titles import TitleIndex

log = logging.getLogger(__name__)


def load_chat_topics(path: str) -> dict[str, list[str]]:
    """Тематики из истории чата (parsers/chat.py), опционально."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        topics = json.load(f).get("topics", {})
    log.info("Тематик из чата: %d", len(topics))
    return topics


class Retriever:
    def __init__(self, collection, tg_collection, embed, title_index: TitleIndex,
                 chat_topics: dict[str, list[str]]):
        self.collection = collection
        self.tg_collection = tg_collection  # None — коллекция недоступна
        self.embed = embed
        self.title_index = title_index
        self.chat_topics = chat_topics

    def genshin(self, query: str) -> str:
        """Статьи по названию + векторный поиск по вики + keyword-поиск по данным из чата."""
        parts = []
        q_emb = self.embed([query])
        seen_ids: set[str] = set()
        wiki: list[tuple[str, str]] = []

        def add(results) -> None:
            for id_, doc, meta in zip(results["ids"][0], results["documents"][0], results["metadatas"][0], strict=True):
                if id_ not in seen_ids:
                    seen_ids.add(id_)
                    wiki.append(((meta or {}).get("title", ""), doc))

        # 1. Статьи, названия которых упомянуты в вопросе (Лиза, Райдэн, ...)
        titles = self.title_index.find(query)
        for title in titles:
            add(self.collection.query(query_embeddings=q_emb, n_results=TITLE_K, where={"title": title}))

        # 2. Векторный поиск по всей вики
        add(self.collection.query(query_embeddings=q_emb, n_results=TOP_K))
        log.info("Контекст: статьи по названию %s, всего чанков %d", titles, len(wiki))

        if wiki:
            wiki_block = "\n\n---\n\n".join(f"[Вики: {t}]\n{d}" for t, d in wiki)
            parts.append(f"## Данные из вики:\n{wiki_block}")

        # 3. Данные из чата
        chat_block = self._chat(query)
        if chat_block:
            parts.append(chat_block)
        return "\n\n".join(parts)

    def _chat(self, query: str) -> str:
        if not self.chat_topics:
            return ""
        query_words = set(query.lower().split())
        scored = sorted(
            self.chat_topics.items(),
            key=lambda kv: len(query_words & set(kv[0].lower().split())),
            reverse=True,
        )
        chat_parts = []
        for topic, msgs in scored[:2]:
            block = "\n".join(f"  - {m}" for m in msgs[:5])
            chat_parts.append(f"[Чат: {topic}]\n{block}")
        return "## Данные из чата:\n" + "\n\n".join(chat_parts)

    def tainted_grail(self, query: str) -> str:
        if self.tg_collection is None:
            return ""
        r = self.tg_collection.query(query_embeddings=self.embed([query]), n_results=TG_TOP_K)
        metas = [(m or {}) for m in r["metadatas"][0]]
        log.info("Контекст TG: %s", [m.get("title") for m in metas])
        parts = [f"[{m.get('title', '')}]\n{d}" for d, m in zip(r["documents"][0], metas, strict=True)]
        return "## Данные по «Осквернённому Граалю»:\n" + "\n\n---\n\n".join(parts)
