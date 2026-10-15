import pytest

from bot.access import Access, parse_allowed_users


def test_parse_ids_with_optional_labels():
    assert parse_allowed_users(" 123 , 456:@name ,789: Мария (сестра) ,") == {
        123: "", 456: "@name", 789: "Мария (сестра)"}
    assert parse_allowed_users("") == {}


def test_parse_rejects_bad_id():
    with pytest.raises(SystemExit, match="@name"):
        parse_allowed_users("123,@name")


def test_private_only_by_list():
    access = Access({-100}, {42: "@friend"})
    assert access.private_allowed(42)
    assert not access.private_allowed(7)  # участник чата без записи в списке — нет
    assert access.label(42) == "@friend" and access.label(7) == ""


def test_chats():
    assert Access({-100}).chat_allowed(-100) and not Access({-100}).chat_allowed(-200)
    assert Access(set()).unrestricted and Access(set()).chat_allowed(-200)
