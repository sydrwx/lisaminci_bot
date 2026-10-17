import asyncio

from bot.dialogs import Dialogs
from bot.prompt import build_messages, build_system_prompt, load_private_lines
from bot.topics import Topic
from common.state import StateStore
from tests.fakes import FakeCollection


def test_private_lines_skip_comments_and_blanks(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("# комментарий\n\n@a — друг\n  @b — враг  \n", encoding="utf-8")
    assert load_private_lines(str(f)) == ["@a — друг", "@b — враг"]
    assert load_private_lines(str(tmp_path / "нет.txt")) == []


def test_private_lines_in_system_prompt():
    assert "@a — друг" in build_system_prompt(["@a — друг"])


def test_build_messages_caches_history_and_attaches_context():
    history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    msgs = build_messages(history, "Вопрос", "КОНТЕКСТ", "ИНФО")
    assert history[-1]["content"] == "a1"  # история не меняется
    assert msgs[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[-1] == {"role": "user", "content": "Контекст:\nКОНТЕКСТ\n\nИНФО\n\nВопрос"}


def test_dialogs_keep_last_pairs_and_topic():
    d = Dialogs(max_pairs=2)
    for i in range(3):
        asyncio.run(d.remember((1, 1), f"q{i}", f"a{i}", Topic.TG))
    assert [m["content"] for m in d.history((1, 1))] == ["q1", "a1", "q2", "a2"]
    assert d.last_topic((1, 1)) is Topic.TG
    asyncio.run(d.reset((1, 1)))
    assert d.history((1, 1)) == [] and d.last_topic((1, 1)) is None


def test_dialogs_survive_restart_and_reset_clears_db():
    store = StateStore(FakeCollection())
    d = Dialogs(store, max_pairs=2)
    asyncio.run(d.remember((-100123, 5), "q", "a", Topic.TG))
    asyncio.run(d.remember((7, 7), "q2", "a2", Topic.GENSHIN))
    restored = Dialogs(store, max_pairs=2)
    assert [m["content"] for m in restored.history((-100123, 5))] == ["q", "a"]
    assert restored.last_topic((-100123, 5)) is Topic.TG
    asyncio.run(restored.reset((-100123, 5)))
    again = Dialogs(store, max_pairs=2)
    assert again.history((-100123, 5)) == [] and again.last_topic((-100123, 5)) is None
    assert again.history((7, 7))  # другие диалоги не задеты
    # MAX_HISTORY уменьшили — при загрузке лишнее отрезается
    assert len(Dialogs(store, max_pairs=1).history((7, 7))) == 2


def test_confidentiality_in_prompt_and_admin_note_only_for_admin():
    from bot.prompt import CONFIDENTIALITY, system_blocks

    prompt = build_system_prompt([])
    assert CONFIDENTIALITY[0] in prompt
    assert [b["text"] for b in system_blocks(prompt)] == [prompt]
    admin = system_blocks(prompt, "ПОМЕТКА")
    assert admin[0] == system_blocks(prompt)[0]  # кэш основного промпта общий
    assert admin[1] == {"type": "text", "text": "ПОМЕТКА"}


def test_admin_note_from_file(tmp_path):
    from bot.prompt import load_admin_note

    f = tmp_path / "admin.txt"
    f.write_text("# комментарий\nПервая строка.\n\nВторая.\n", encoding="utf-8")
    assert load_admin_note(str(f)) == "Первая строка. Вторая."
    assert load_admin_note(str(tmp_path / "нет.txt")) is None
