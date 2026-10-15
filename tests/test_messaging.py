from types import SimpleNamespace

from bot.messaging import describe_user, is_admin, prompt_user


def user(username=None, name="Эмбер", id_=123456789):
    return SimpleNamespace(username=username, full_name=name, id=id_)


def test_prompt_user_never_contains_id():
    assert prompt_user(user()) == "Эмбер (без username)"
    assert "123456789" not in prompt_user(user())
    assert prompt_user(user("amber")) == "@amber (Эмбер)"
    assert "123456789" in describe_user(user())  # в логах id остаётся


def test_is_admin_by_username_case_insensitive():
    assert is_admin(user("Hardkov"), {"hardkov"})
    assert not is_admin(user(), {"hardkov"})
    assert not is_admin(None, {"hardkov"})
