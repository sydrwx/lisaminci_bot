"""Доступ: Лиза отвечает в разрешённых чатах, а в личке — только пользователям из ALLOWED_USERS."""

from __future__ import annotations

from collections.abc import Collection, Mapping


def parse_allowed_users(value: str) -> dict[int, str]:
    """«id[:метка],...» → {id: метка}. id обязателен и проверяется, метка — любой текст для логов."""
    users: dict[int, str] = {}
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        id_part, _, label = entry.partition(":")
        try:
            user_id = int(id_part.strip())
        except ValueError:
            raise SystemExit(f"ALLOWED_USERS: «{entry}» — ожидается id пользователя "
                             "(число), метка через двоеточие необязательна: 123456789:@name") from None
        users[user_id] = label.strip()
    return users


class Access:
    def __init__(self, allowed_chats: Collection[int], allowed_users: Mapping[int, str] | None = None):
        self.allowed_chats = allowed_chats
        self.allowed_users = allowed_users or {}

    @property
    def unrestricted(self) -> bool:
        return not self.allowed_chats

    def chat_allowed(self, chat_id: int) -> bool:
        return self.unrestricted or chat_id in self.allowed_chats

    def private_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_users

    def label(self, user_id: int) -> str:
        """Метка пользователя из ALLOWED_USERS (только для логов), «» — если нет."""
        return self.allowed_users.get(user_id, "")
