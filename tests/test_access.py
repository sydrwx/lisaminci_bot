import asyncio
from types import SimpleNamespace

from bot.access import Access


class FakeBot:
    def __init__(self, members: dict[int, str]):
        self.members = members
        self.calls = 0

    async def get_chat_member(self, chat_id, user_id):
        self.calls += 1
        return SimpleNamespace(status=self.members.get(user_id, "left"))


def test_allowed_user_without_membership():
    bot = FakeBot({})
    access = Access({-100}, allowed_users={42})
    assert asyncio.run(access.private_allowed(bot, 42))
    assert bot.calls == 0  # членство не проверяется


def test_member_allowed_stranger_denied():
    bot = FakeBot({7: "member"})
    access = Access({-100}, allowed_users={42})
    assert asyncio.run(access.private_allowed(bot, 7))
    assert not asyncio.run(access.private_allowed(bot, 8))
