"""
parsers/chat.py — Парсинг истории Telegram-группы и кластеризация по тематикам

Использование:
    pip install telethon anthropic python-dotenv
    python -m parsers.chat

Результат: knowledge_base.json — структурированная база знаний по тематикам
"""

import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Message
import anthropic

from common.ipcheck import check_ip

load_dotenv(override=False)

# Claude API — только с IP из разрешённой подсети (IPCheckError прерывает запуск)
check_ip()

# ─── Конфигурация ────────────────────────────────────────────────────────────
API_ID            = int(os.getenv("TG_API_ID"))
API_HASH          = os.getenv("TG_API_HASH")
PHONE             = os.getenv("TG_PHONE")
CHAT_TARGET       = os.getenv("TG_CHAT")
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_PROXY   = os.getenv("ANTHROPIC_PROXY")  # HTTP-прокси только для Claude API
OUTPUT_FILE       = "./data/knowledge_base.json"

BATCH_SIZE   = 100
MAX_MESSAGES = 5000  # 0 = без лимита
# ─────────────────────────────────────────────────────────────────────────────

client_tg = TelegramClient("./data/session_parser", API_ID, API_HASH)
client_ai = anthropic.Anthropic(
    api_key=ANTHROPIC_KEY,
    base_url="https://api.anthropic.com",
    **({"http_client": anthropic.DefaultHttpxClient(proxy=ANTHROPIC_PROXY)} if ANTHROPIC_PROXY else {}),
)


def get_topic_id(msg: Message) -> int | None:
    """
    Определить к какому топику относится сообщение.
    Корневой топик: reply_to_top_id=None → топик = reply_to_msg_id
    Подтопик:       reply_to_top_id=X    → топик = reply_to_top_id
    Сообщение без топика → None (General, id=1)
    """
    if not msg.reply_to:
        return None
    r = msg.reply_to
    if not getattr(r, 'forum_topic', False):
        return None
    top = getattr(r, 'reply_to_top_id', None)
    return top if top else r.reply_to_msg_id


async def fetch_messages(chat) -> list[dict]:
    """Скачать все сообщения и разметить по топикам."""
    print(f"📥 Загружаю все сообщения чата...")
    messages = []
    limit = MAX_MESSAGES or None

    async for msg in client_tg.iter_messages(chat, limit=limit):
        if not isinstance(msg, Message) or not msg.text:
            continue

        topic_id = get_topic_id(msg)
        messages.append({
            "id":       msg.id,
            "date":     msg.date.isoformat(),
            "sender":   str(msg.sender_id),
            "topic_id": topic_id,   # числовой ID топика (None = General)
            "topic":    f"Топик {topic_id}" if topic_id else "General",
            "text":     msg.text.strip(),
        })

    messages.reverse()  # хронологический порядок

    # Статистика по топикам
    topic_counts: dict[str, int] = {}
    for m in messages:
        topic_counts[m["topic"]] = topic_counts.get(m["topic"], 0) + 1

    print(f"✅ Загружено {len(messages)} сообщений из {len(topic_counts)} топиков:")
    for t, c in sorted(topic_counts.items(), key=lambda x: -x[1]):
        print(f"   {t}: {c} сообщений")

    return messages


def cluster_batch(messages: list[dict]) -> dict:
    block = "\n".join(
        f"[{m['date'][:10]}][{m.get('topic', '')}] {m['text']}" for m in messages
    )

    prompt = f"""Ты — аналитик переписки. Получи список сообщений из Telegram-чата.
Твоя задача:
1. Выдели от 3 до 10 ключевых тематик, которые обсуждаются в этих сообщениях.
2. Распредели каждое сообщение по наиболее подходящей тематике.
3. Верни ТОЛЬКО валидный JSON без пояснений в формате:
{{
  "Название тематики": ["сообщение 1", "сообщение 2", ...],
  ...
}}

Сообщения:
{block}
"""

    response = client_ai.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def merge_clusters(existing: dict, new_batch: dict) -> dict:
    for topic, msgs in new_batch.items():
        matched = next(
            (k for k in existing if topic.lower() in k.lower()
             or k.lower() in topic.lower()), None
        )
        key = matched if matched else topic
        existing.setdefault(key, []).extend(msgs)
    return existing


def deduplicate(kb: dict) -> dict:
    return {topic: list(dict.fromkeys(msgs)) for topic, msgs in kb.items()}


def save_knowledge_base(kb: dict, meta: dict):
    output = {
        "meta": meta,
        "topics": kb,
        "stats": {
            "total_topics": len(kb),
            "total_messages": sum(len(v) for v in kb.values()),
        },
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"💾 База знаний сохранена в {OUTPUT_FILE}")
    print(f"   Тематик: {output['stats']['total_topics']}")
    print(f"   Сообщений: {output['stats']['total_messages']}")


async def main():
    await client_tg.start(phone=PHONE)
    print("🔐 Авторизация в Telegram прошла успешно")

    print("🔄 Загружаю список диалогов...")
    await client_tg.get_dialogs()

    chat = await client_tg.get_entity(int(CHAT_TARGET))
    meta = {
        "chat":       CHAT_TARGET,
        "chat_title": getattr(chat, "title", CHAT_TARGET),
        "parsed_at":  datetime.utcnow().isoformat(),
    }

    messages = await fetch_messages(chat)
    if not messages:
        print("⚠️  Нет сообщений для обработки")
        return

    knowledge_base = {}
    total_batches = (len(messages) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(messages), BATCH_SIZE):
        batch = messages[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"🔍 Обрабатываю батч {batch_num}/{total_batches} ({len(batch)} сообщений)...")
        try:
            clusters = cluster_batch(batch)
            knowledge_base = merge_clusters(knowledge_base, clusters)
        except Exception as e:
            print(f"   ⚠️  Ошибка в батче {batch_num}: {e}")

    knowledge_base = deduplicate(knowledge_base)
    save_knowledge_base(knowledge_base, meta)
    await client_tg.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
