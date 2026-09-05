"""Скелет просмотрщика Instagram-сторис (подзадача A2, решение пользователя).

ПЛАН РЕАЛИЗАЦИИ (следующий этап):
- Просмотр IG-сторис через сторонний сайт-парсер (без официального API и
  без instagrapi): базовый URL сервиса берётся из env
  ``INSTAGRAM_VIEWER_BASE_URL`` (см. config.py, секция «Опциональные
  параметры для будущих сервисов»).
- Контракт: ``fetch_stories(username) -> list[dict]``; каждый элемент —
  ``{"media_url": str, "type": "photo" | "video", "taken_at": int}``
  (unix-время публикации в секундах).
- Ожидаемое поведение будущей реализации:
  1) нормализовать/валидировать username (см. normalize_username ниже);
  2) если INSTAGRAM_VIEWER_BASE_URL пуст — вернуть ошибку конфигурации;
  3) HTTP-запрос к парсеру в executor (requests, timeout из config);
  4) нормализовать ответ парсера к контракту выше; секреты не логировать.
- Тело fetch_stories поднимает NotImplementedError("Instagram-просмотр
  будет реализован отдельным этапом") — контракт зафиксирован, вызовы
  можно уже писать в коде.

Рядом — рабочая чистая утилита normalize_username (нормализация/валидация
по аналогии с utils.normalize_tiktok_username), чтобы контракт был готов
к тестам без реализации сетевой части.
"""
import re

# Username Instagram: 1-30 символов, буквы/цифры/точка/подчёркивание,
# не начинается с точки; префикс '@' и ссылка instagram.com/<name> допускаются на входе.
INSTAGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_username(raw_text: str) -> str:
    """Извлекает и нормализует Instagram-username из ника/ссылки; '' если невалидно.

    Принимает '@nick', 'nick', 'https://www.instagram.com/nick/?...'.
    Результат: 1-30 символов [A-Za-z0-9._], нижний регистр; '' при невалидном вводе.
    """
    text = (raw_text or "").strip()
    if not text:
        return ""

    if "instagram.com" in text.lower():
        match = re.search(r"instagram\.com/([A-Za-z0-9._]+)", text, flags=re.IGNORECASE)
        if match:
            text = match.group(1)

    text = text.strip().strip("/")
    if text.startswith("@"):
        text = text[1:]
    if "/" in text:
        text = text.split("/", 1)[0]
    if "?" in text:
        text = text.split("?", 1)[0]

    text = text.strip().lower()
    if not text:
        return ""

    if not INSTAGRAM_USERNAME_RE.fullmatch(text):
        return ""

    return text


async def fetch_stories(username: str) -> list[dict]:
    """Возвращает сторис пользователя в формате контракта (см. docstring модуля).

    Формат элемента: {"media_url": str, "type": "photo" | "video", "taken_at": int}.

    Безопасное отключение (Этап 6): если config.INSTAGRAM_VIEWER_BASE_URL
    пуст — сервис не настроен, возвращаем [] без исключения (вызовы можно
    оставлять в коде; реальная реализация парсера — отдельный этап).
    """
    import config  # локальный импорт: чтение env-настройки на вызове

    if not (config.INSTAGRAM_VIEWER_BASE_URL or "").strip():
        return []

    raise NotImplementedError("Instagram-просмотр будет реализован отдельным этапом")
