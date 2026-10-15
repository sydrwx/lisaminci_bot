"""Состояние бота (молитвы, id сообщений для /delete) в отдельной коллекции ChromaDB.

Chroma — векторная база, поэтому у записей фиктивный эмбеддинг; значение хранится
JSON-строкой в документе, вид записи — в метаданных. Id записи — «вид:ключ».
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from common.db import get_client

log = logging.getLogger(__name__)

_DUMMY_EMBEDDING = [1.0]


class StateStore:
    def __init__(self, collection):
        self._col = collection

    @classmethod
    def connect(cls, name: str) -> StateStore:
        return cls(get_client().get_or_create_collection(name, embedding_function=None))

    def load_all(self, kind: str) -> dict[str, Any]:
        """Все записи вида kind: ключ → значение."""
        got = self._col.get(where={"kind": kind}, include=["documents"])
        prefix = f"{kind}:"
        return {id_.removeprefix(prefix): json.loads(doc)
                for id_, doc in zip(got["ids"], got["documents"], strict=True)}

    def put(self, kind: str, key: str, value: Any) -> None:
        self._upsert(kind, key, json.dumps(value, ensure_ascii=False))

    async def save(self, kind: str, key: str, value: Any) -> None:
        """put в отдельном потоке (HTTP к Chroma синхронный); сбой — только в лог."""
        # Сериализуем здесь: value может меняться в цикле событий, пока идёт запись
        doc = json.dumps(value, ensure_ascii=False)
        try:
            await asyncio.to_thread(self._upsert, kind, key, doc)
        except Exception as e:
            log.warning("Не удалось сохранить %s:%s в базу: %r", kind, key, e)

    def _upsert(self, kind: str, key: str, doc: str) -> None:
        self._col.upsert(ids=[f"{kind}:{key}"], documents=[doc], metadatas=[{"kind": kind}],
                         embeddings=[_DUMMY_EMBEDDING])
