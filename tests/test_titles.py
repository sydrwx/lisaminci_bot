from bot.titles import TitleIndex, stem


def test_stem_cuts_case_endings():
    assert stem("Лизы") == stem("Лизе") == stem("Лиза") == "лиз"
    assert stem("Ёимии") == "еим"
    assert stem("Ноэлль") == "ноэлл"


def index(*titles):
    return TitleIndex(lambda: titles)


def test_find_title_in_another_case():
    assert index("Лиза", "Джинн").find("Что носит Лизе?") == ["Лиза"]


def test_longer_title_wins_on_same_words():
    assert index("Райдэн", "Сёгун Райдэн").find("билд на сёгуна райдэн") == ["Сёгун Райдэн"]


def test_max_matches():
    idx = TitleIndex(lambda: ["Лиза", "Джинн", "Кэйа", "Дилюк"], max_matches=2)
    assert len(idx.find("Лиза Джинн Кэйа Дилюк")) == 2


def test_short_titles_ignored():
    assert index("Ци").find("ци") == []


def test_reloads_after_ttl():
    calls = []

    def load():
        calls.append(1)
        return ["Лиза"]

    idx = TitleIndex(load, ttl=-1)
    idx.find("Лиза")
    idx.find("Лиза")
    assert len(calls) == 2
