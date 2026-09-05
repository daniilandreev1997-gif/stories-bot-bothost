"""Пакет instagram: скелет просмотрщика IG-сторис (подзадача A2).

- viewer: normalize_username (рабочая утилита) + fetch_stories (контракт,
  тело поднимает NotImplementedError до отдельного этапа реализации).

Реэкспорт публичных имён для импорта вида ``from instagram import ...``.
"""
from .viewer import fetch_stories, normalize_username

__all__ = [
    "fetch_stories",
    "normalize_username",
]
