"""История диалогов: в памяти процесса и в базе (записи вида «dialog»), переживает перезапуск.

Ключ — (чат, пользователь): личка и каждая группа — отдельные диалоги, контекст не смешивается.
В истории только вопросы и ответы, без найденного контекста.
"""

from __future__ import annotations

from bot.config import MAX_HISTORY
from bot.topics import Topic
from common.state import StateStore

DialogKey = tuple[int, int]


def _db_key(key: DialogKey) -> str:
    return f"{key[0]}:{key[1]}"


class Dialogs:
    KIND = "dialog"

    def __init__(self, store: StateStore | None = None, max_pairs: int = MAX_HISTORY):
        self.store = store
        self._max_messages = max_pairs * 2
        self._history: dict[DialogKey, list[dict]] = {}
        self._topic: dict[DialogKey, Topic] = {}
        for db_key, rec in (store.load_all(self.KIND) if store else {}).items():
            chat, user = db_key.rsplit(":", 1)
            key = (int(chat), int(user))
            self._history[key] = rec.get("history", [])[-self._max_messages:]
            if rec.get("topic") in Topic.__members__.values():
                self._topic[key] = Topic(rec["topic"])

    def history(self, key: DialogKey) -> list[dict]:
        return self._history.get(key, [])

    def last_topic(self, key: DialogKey) -> Topic | None:
        return self._topic.get(key)

    async def remember(self, key: DialogKey, question: str, answer: str, topic: Topic) -> None:
        history = self._history.setdefault(key, [])
        history.extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ])
        del history[:-self._max_messages]
        self._topic[key] = topic
        if self.store:
            await self.store.save(self.KIND, _db_key(key), {"history": history, "topic": topic.value})

    async def reset(self, key: DialogKey) -> None:
        self._history.pop(key, None)
        self._topic.pop(key, None)
        if self.store:
            await self.store.delete(self.KIND, _db_key(key))
