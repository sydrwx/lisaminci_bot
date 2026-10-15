from bot.topics import Topic, detect_topic

TG_CHAT = -100
OTHER = -200


def detect(query, chat=OTHER, last=None, title=False):
    return detect_topic(query, chat, last, {TG_CHAT}, lambda q: title)


def test_tg_markers():
    assert detect("Как пройти менгир?") is Topic.TG
    assert detect("Кто такая Венеда?") is Topic.TG


def test_default_genshin():
    assert detect("Лучший билд Лизы") is Topic.GENSHIN


def test_priority_chat_defaults_to_tg_unless_genshin_marker():
    assert detect("Как ходить?", chat=TG_CHAT) is Topic.TG
    assert detect("Какие сейчас баннеры?", chat=TG_CHAT) is Topic.GENSHIN


def test_follow_up_stays_in_tg_unless_wiki_title():
    assert detect("А дальше?", last=Topic.TG) is Topic.TG
    assert detect("А Лиза?", last=Topic.TG, title=True) is Topic.GENSHIN
