from datetime import datetime

from bot.game_info import SERVER_TZ, BannerStore, current_game_info
from common.state import StateStore
from tests.fakes import FakeCollection

DATA = {
    "generated": "2026-10-15T00:00:00",
    "banners": [
        {"type": "Молитва события персонажа", "name": "Сейчас", "featured": ["Фурина"],
         "featured_rare": ["Беннет"], "start": "2026-10-01 10:00:00", "end": "2026-10-20 18:00:00"},
        {"type": "Молитва события персонажа", "name": "Потом", "featured": ["Лиза"],
         "start": "2026-10-21 10:00:00", "end": "2026-11-10 18:00:00", "version": "6.2"},
    ],
    "events": [{"name": "Фестиваль", "start": "2026-10-10 10:00:00", "end": "2026-10-25 04:00:00"}],
}
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=SERVER_TZ)


def test_current_and_upcoming():
    text = current_game_info(DATA, NOW)
    assert "«Сейчас», до 2026-10-20 18:00: 5★ Фурина; 4★: Беннет" in text
    assert "«Потом», версия 6.2, с 2026-10-21 10:00" in text
    assert "- Фестиваль (до 2026-10-25)" in text


def test_no_data_only_date():
    assert current_game_info({}, NOW) == "## Сейчас в игре\nСегодня: 2026-10-15 12:00 (UTC+8, сервер Азия)"


def test_banner_store_reads_db_and_caches():
    db = StateStore(FakeCollection())
    banners = BannerStore(db, ttl=60)
    assert banners.load() == {}
    db.put("banners", "current", DATA)
    assert banners.load() == {}  # кэш ещё действует
    banners.ttl = -1
    assert banners.current_character_banner(NOW)["name"] == "Сейчас"


def test_banner_store_keeps_last_snapshot_when_db_down():
    class Down(FakeCollection):
        def get(self, *a, **kw):
            raise ConnectionError("chroma down")

    db = StateStore(FakeCollection())
    db.put("banners", "current", DATA)
    banners = BannerStore(db, ttl=-1)
    banners.load()
    banners.store = StateStore(Down())
    assert banners.load() == DATA
