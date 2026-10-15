from datetime import datetime

from bot.game_info import SERVER_TZ, current_game_info

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
