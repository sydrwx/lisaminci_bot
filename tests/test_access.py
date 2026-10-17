import asyncio
from types import SimpleNamespace

from bot.access import Access


class FakeBot:
    def __init__(self, members):
        self.members = members  # {(chat_id, user_id): status}
        self.calls = 0

    async def get_chat_member(self, chat_id, user_id):
        self.calls += 1
        if (chat_id, user_id) not in self.members:
            raise RuntimeError("user not found")
        return SimpleNamespace(status=self.members[(chat_id, user_id)], is_member=True)


def test_private_only_for_members_of_allowed_chats():
    bot = FakeBot({(-200, 1): "member", (-100, 2): "left", (-100, 3): "restricted"})
    access = Access([-100, -200])
    assert asyncio.run(access.private_allowed(bot, 1))   # состоит во втором чате
    assert not asyncio.run(access.private_allowed(bot, 2))  # вышел
    assert asyncio.run(access.private_allowed(bot, 3))   # ограничен, но в чате
    assert not asyncio.run(access.private_allowed(bot, 4))  # чужой


def test_membership_cached():
    bot = FakeBot({(-100, 1): "member"})
    access = Access([-100], cache_ttl=100, negative_ttl=0)
    asyncio.run(access.private_allowed(bot, 1))
    asyncio.run(access.private_allowed(bot, 1))
    assert bot.calls == 1
    asyncio.run(access.private_allowed(bot, 2))
    asyncio.run(access.private_allowed(bot, 2))  # «нет» с negative_ttl=0 — перепроверяется
    assert bot.calls == 3


def test_chats():
    assert Access({-100}).chat_allowed(-100) and not Access({-100}).chat_allowed(-200)
    assert Access(set()).unrestricted and Access(set()).chat_allowed(-200)
