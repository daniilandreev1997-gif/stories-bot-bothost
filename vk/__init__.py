"""Пакет vk: клиент VK API, работа со сторис, прямая авторизация по логину/паролю.

Состав (подзадача A2):
- client: vk_call + check_token_works_for_stories;
- stories: get_vk_stories / send_stories / check_and_send_new_vk / checknow_send_all_vk;
- auth: vk_direct_auth — чистый клиент direct-авторизации (2FA/капча отдельно).

Все публичные имена реэкспортированы для импорта вида ``from vk import ...``.
"""
from .client import check_token_works_for_stories, vk_call
from .stories import check_and_send_new_vk, checknow_send_all_vk, get_vk_stories, send_stories
from .auth import vk_direct_auth

__all__ = [
    "vk_call",
    "check_token_works_for_stories",
    "get_vk_stories",
    "send_stories",
    "check_and_send_new_vk",
    "checknow_send_all_vk",
    "vk_direct_auth",
]
