"""История диалогов в памяти процесса.

Ключ — (чат, пользователь): личка и каждая группа — отдельные диалоги, контекст не смешивается.
В истории только вопросы и ответы, без найденного контекста.
"""

from __future__ import annotations

from bot.config import MAX_HISTORY
from bot.topics import Topic

DialogKey = tuple[int, int]


class Dialogs:
    def __init__(self, max_pairs: int = MAX_HISTORY):
        self._max_messages = max_pairs * 2
        self._history: dict[DialogKey, list[dict]] = {}
        self._topic: dict[DialogKey, Topic] = {}

    def history(self, key: DialogKey) -> list[dict]:
        return self._history.get(key, [])

    def last_topic(self, key: DialogKey) -> Topic | None:
        return self._topic.get(key)

    def remember(self, key: DialogKey, question: str, answer: str, topic: Topic) -> None:
        history = self._history.setdefault(key, [])
        history.extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ])
        del history[:-self._max_messages]
        self._topic[key] = topic

    def reset(self, key: DialogKey) -> None:
        self._history.pop(key, None)
        self._topic.pop(key, None)
