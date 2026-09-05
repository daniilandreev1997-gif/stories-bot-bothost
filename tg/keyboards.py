"""Клавиатуры Telegram-бота: тексты кнопок и главная ReplyKeyboard.

Тексты кнопок перенесены ДОСЛОВНО из bot_host.py (подзадача A2) —
изменение строк недопустимо (сравнение ввода в handle_text опирается
на точное совпадение).
"""
from telegram import ReplyKeyboardMarkup

BUTTON_VK_ID = "VK ID"
BUTTON_TIKTOK = "TikTok @username"
BUTTON_TOKEN_VK = "Token VK"
BUTTON_CHECK_NOW = "Проверить сейчас"
BUTTON_LIST = "Кого мониторю"
BUTTON_SILENT = "Тихий режим"
BUTTON_CLEAR_TOKEN = "Сброс VK token"
BUTTON_TIKTOK_RESET = "Сброс TikTok истории"
BUTTON_TIKTOK_LOGIN = "TikTok вход (логин+пароль)"
BUTTON_VK_LOGIN = "🔑 VK вход по логину"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BUTTON_VK_ID, BUTTON_TIKTOK],
        [BUTTON_TOKEN_VK, BUTTON_CLEAR_TOKEN],
        [BUTTON_CHECK_NOW, BUTTON_LIST],
        [BUTTON_TIKTOK_RESET, BUTTON_SILENT],
        [BUTTON_TIKTOK_LOGIN, BUTTON_VK_LOGIN],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

WAIT_STATE_KEYS = (
    "await_vk_id",
    "await_vk_token",
    "await_tiktok_username",
    "await_tiktok_login",
    "await_vk_login",
    "await_vk_code",
    "await_vk_captcha",
)
