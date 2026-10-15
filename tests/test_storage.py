import asyncio
from types import SimpleNamespace

from bot.messaging import SentMessages
from bot.storage import StateStore
from tests.fakes import FakeCollection


def test_put_and_load_by_kind():
    store = StateStore(FakeCollection())
    store.put("pray", "1", {"total": 3, "username": "Лиза"})
    store.put("sent", "1", [10])
    assert store.load_all("pray") == {"1": {"total": 3, "username": "Лиза"}}
    assert store.load_all("sent") == {"1": [10]}


def test_save_swallows_errors():
    class Broken(FakeCollection):
        def upsert(self, *a, **kw):
            raise ConnectionError("chroma down")

    asyncio.run(StateStore(Broken()).save("sent", "1", [1]))  # не падает


def test_sent_messages_track_limit_and_pop():
    store = StateStore(FakeCollection())
    sent = SentMessages(store, max_per_chat=2)

    async def run():
        for mid in (1, 2, 3):
            await sent.track(SimpleNamespace(chat_id=-100, message_id=mid))

    asyncio.run(run())
    assert store.load_all("sent") == {"-100": [2, 3]}
    # после перезапуска id читаются из базы
    restored = SentMessages(store)
    assert restored.pop(-100) == 3 and restored.pop(-100) == 2 and restored.pop(-100) is None
