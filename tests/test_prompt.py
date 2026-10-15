from bot.dialogs import Dialogs
from bot.prompt import build_messages, build_system_prompt, load_private_lines
from bot.topics import Topic


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
        d.remember((1, 1), f"q{i}", f"a{i}", Topic.TG)
    assert [m["content"] for m in d.history((1, 1))] == ["q1", "a1", "q2", "a2"]
    assert d.last_topic((1, 1)) is Topic.TG
    d.reset((1, 1))
    assert d.history((1, 1)) == [] and d.last_topic((1, 1)) is None
