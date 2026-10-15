"""Настройки бота из переменных окружения (и .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

BOT_NAME = "Лиза"
CLAUDE_BASE_URL = "https://api.anthropic.com"  # ANTHROPIC_BASE_URL игнорируется

TOP_K = 5                # чанков векторного поиска по всей вики
TG_TOP_K = 6             # чанков по «Осквернённому Граалю»
TITLE_K = 4              # чанков из каждой статьи, найденной по названию (вики + данные игры)
MAX_TITLE_MATCHES = 3    # сколько статей брать по совпадению названия
TITLES_TTL = 3600        # как часто перечитывать список статей (updater меняет базу)
MAX_HISTORY = 10         # пар вопрос-ответ в истории диалога
MEMBER_CACHE_TTL = 3600  # кэш проверки членства в чате для лички
MAX_TRACKED = 50         # сколько последних сообщений бота помнить в каждом чате для /delete
MAX_TOKENS = 1000        # длина ответа Claude


def _ids(value: str) -> frozenset[int]:
    return frozenset(int(c) for c in value.split(",") if c.strip())


def _usernames(value: str) -> frozenset[str]:
    return frozenset(u.strip().lstrip("@").lower() for u in value.split(",") if u.strip())


@dataclass(frozen=True)
class Settings:
    bot_token: str
    anthropic_key: str
    claude_model: str
    anthropic_proxy: str | None        # HTTP-прокси только для Claude API

    kb_file: str                       # данные из чата (parsers/chat.py), опционально
    private_prompt_file: str           # кто есть кто в чате, вне git

    allowed_chats: frozenset[int]      # где Лиза отвечает; в личке — участникам этих чатов
    tg_priority_chats: frozenset[int]  # где по умолчанию обсуждают «Осквернённый Грааль»
    admin_usernames: frozenset[str]    # кому разрешено /delete
    pray_chat_id: int                  # где работают /pray и /praystats (0 — нигде)
    pray_bonus_users: frozenset[str]   # бонусный шанс на Джинн

    ip_check_interval: int             # сек, пока IP в порядке
    ip_recheck_interval: int           # сек, пока Акаша недоступен
    proxy_check_timeout: float         # сек, проверка прокси перед Claude
    net_check_interval: int            # сек, фоновая проверка Telegram и прокси

    @classmethod
    def from_env(cls) -> Settings:
        # Переменные окружения (ANTHROPIC_API_KEY из ~/.bashrc) приоритетнее .env
        load_dotenv(override=False)
        env = os.getenv
        anthropic_key = env("ANTHROPIC_API_KEY")
        if not anthropic_key:
            raise SystemExit("ANTHROPIC_API_KEY не задан — выполни `source ~/.bashrc`")
        return cls(
            bot_token=env("TG_BOT_TOKEN", ""),
            anthropic_key=anthropic_key,
            claude_model=env("CLAUDE_MODEL", "claude-sonnet-4-5"),
            anthropic_proxy=env("ANTHROPIC_PROXY") or None,
            kb_file="./data/knowledge_base.json",
            private_prompt_file=env("PRIVATE_PROMPT_FILE", "./data/prompt_private.txt"),
            allowed_chats=_ids(env("ALLOWED_CHATS", env("TG_CHAT", ""))),
            tg_priority_chats=_ids(env("TG_PRIORITY_CHATS", "")),
            admin_usernames=_usernames(env("ADMIN_USERNAMES", "")),
            pray_chat_id=int(env("PRAY_CHAT_ID", "0")),
            pray_bonus_users=_usernames(env("PRAY_BONUS_USERS", "")),
            ip_check_interval=int(env("IP_CHECK_INTERVAL", "300")),
            ip_recheck_interval=int(env("IP_RECHECK_INTERVAL", "60")),
            proxy_check_timeout=float(env("PROXY_CHECK_TIMEOUT", "3")),
            net_check_interval=int(env("NET_CHECK_INTERVAL", "30")),
        )
