"""Хранение небольших JSON-файлов состояния бота (botdata/)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def load_json(path: str, default: Any) -> Any:
    """Прочитать JSON; нет файла или он битый — default."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data: Any) -> None:
    """Атомарно записать JSON (через временный файл); ошибку записи — только в лог."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Не удалось сохранить %s: %s", path, e)
