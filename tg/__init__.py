"""Пакет tg: Telegram-слой stories-bot-bothost (подзадача A2).

Имя пакета — tg/ (НЕ telegram/), чтобы не затмить библиотеку
python-telegram-bot.

Состав:
- keyboards: тексты кнопок, MAIN_KEYBOARD, WAIT_STATE_KEYS;
- messages: тексты сообщений (константы/строители, дословно из монолита);
- helpers: wait-state, тихий режим, состояние VK-токена, show_main_menu;
- flows: ask_* и set_*_from_text (диалоговые потоки);
- handlers: все командные обработчики и handle_text.

Реэкспорт обработчиков и клавиатур для импорта вида ``from tg import ...``.
"""
from .keyboards import (
    BUTTON_CHECK_NOW,
    BUTTON_CLEAR_TOKEN,
    BUTTON_LIST,
    BUTTON_SILENT,
    BUTTON_TIKTOK,
    BUTTON_TIKTOK_LOGIN,
    BUTTON_TIKTOK_RESET,
    BUTTON_TOKEN_VK,
    BUTTON_VK_ID,
    BUTTON_VK_LOGIN,
    MAIN_KEYBOARD,
    WAIT_STATE_KEYS,
)
from .handlers import (
    checknow_cmd,
    cleartoken_cmd,
    handle_text,
    list_cmd,
    silent_cmd,
    start_cmd,
    tiktok_cmd,
    tiktokreset_cmd,
    token_cmd,
    who_cmd,
)
from .flows import (
    ask_tiktok_login,
    ask_tiktok_username,
    ask_vk_id,
    ask_vk_login,
    ask_vk_token,
    set_tiktok_login_from_text,
    set_tiktok_username_from_text,
    set_vk_captcha_from_text,
    set_vk_code_from_text,
    set_vk_id_from_text,
    set_vk_login_from_text,
    set_vk_token_from_text,
)

__all__ = [
    # keyboards
    "BUTTON_CHECK_NOW",
    "BUTTON_CLEAR_TOKEN",
    "BUTTON_LIST",
    "BUTTON_SILENT",
    "BUTTON_TIKTOK",
    "BUTTON_TIKTOK_LOGIN",
    "BUTTON_TIKTOK_RESET",
    "BUTTON_TOKEN_VK",
    "BUTTON_VK_ID",
    "BUTTON_VK_LOGIN",
    "MAIN_KEYBOARD",
    "WAIT_STATE_KEYS",
    # handlers
    "start_cmd",
    "silent_cmd",
    "list_cmd",
    "who_cmd",
    "checknow_cmd",
    "token_cmd",
    "cleartoken_cmd",
    "tiktok_cmd",
    "tiktokreset_cmd",
    "handle_text",
    # flows
    "ask_vk_id",
    "ask_vk_token",
    "ask_tiktok_login",
    "ask_tiktok_username",
    "ask_vk_login",
    "set_vk_token_from_text",
    "set_vk_id_from_text",
    "set_tiktok_login_from_text",
    "set_tiktok_username_from_text",
    "set_vk_login_from_text",
    "set_vk_code_from_text",
    "set_vk_captcha_from_text",
]
