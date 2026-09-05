"""Пакет tiktok: yt-dlp-извлечение постов, скачивание, отправка и мониторинг.

Состав (подзадача A2):
- extract: get_tiktok_posts — список постов профиля;
- download: download_tiktok_post + build_tiktok_caption;
- monitoring: send_tiktok_post / check_and_send_new_tiktoks и вспомогательные.

Все публичные имена реэкспортированы для импорта вида ``from tiktok import ...``.
"""
from .extract import get_tiktok_posts
from .download import build_tiktok_caption, download_tiktok_post
from .monitoring import check_and_send_new_tiktoks, send_tiktok_fallback, send_tiktok_media_group, send_tiktok_post

__all__ = [
    "get_tiktok_posts",
    "download_tiktok_post",
    "build_tiktok_caption",
    "send_tiktok_post",
    "check_and_send_new_tiktoks",
    "send_tiktok_media_group",
    "send_tiktok_fallback",
]
