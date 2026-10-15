"""Выбор базы для вопроса: Genshin Impact или «Осквернённый Грааль» (Tainted Grail)."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from enum import StrEnum


class Topic(StrEnum):
    GENSHIN = "genshin"
    TG = "tg"


# Маркеры вопроса о настольной игре: тогда ищем в коллекции TG вместо Genshin
TG_RE = re.compile(
    r"tainted|грааль|граал|авалон|avalon|менгир|menhir|искажень|искаженье|wyrd|предтеч"
    r"|корол\w* краха|kings of ruin|путеводн\w* кам|красн\w* смерт|камелот|kamelot|туат"
    r"|беор|айлей|арев|мэггот|ниав|гердвин|осберт|журнал\w* исследован"
    r"|клаудин|венед|нонус|сурлуз|лотиан|элган|фомори",
    re.IGNORECASE,
)

# Явные маркеры Genshin — нужны, чтобы в TG-чатах переключиться на Genshin
GENSHIN_RE = re.compile(
    r"genshin|геншин|тейват|teyvat|мондштадт|ли юэ|инадзум|сумеру|фонтейн|натлан|снежн|нод-?край"
    r"|паймон|примогем|молитв|баннер|крутк|гача|созвезди|возвышени|артефакт|hoyo|хойо",
    re.IGNORECASE,
)


def detect_topic(query: str, chat_id: int, last_topic: Topic | None,
                 tg_priority_chats: Collection[int],
                 mentions_wiki_title: Callable[[str], bool]) -> Topic:
    """Тема вопроса.

    Уточнения без ключевых слов остаются в теме предыдущего вопроса, если в них
    не упомянута статья вики. В чатах из tg_priority_chats Genshin — только по явным маркерам.
    """
    if TG_RE.search(query):
        return Topic.TG
    if chat_id in tg_priority_chats:
        return Topic.GENSHIN if GENSHIN_RE.search(query) else Topic.TG
    if last_topic is Topic.TG and not mentions_wiki_title(query):
        return Topic.TG
    return Topic.GENSHIN
