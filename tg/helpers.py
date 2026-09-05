"""Хелперы Telegram-слоя: wait-state, тихий режим, состояние VK-токена, главное меню.

Логика перенесена дословно из bot_host.py (подзадача A2). В монолите
get_setting/set_setting вызывались без префикса db (NameError Этапа 1) —
здесь они импортируются явно из db, баг устранён.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import db

from .keyboards import MAIN_KEYBOARD, WAIT_STATE_KEYS

logger = logging.getLogger(__name__)


def reset_wait_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Снимает все wait-state ключи из user_data."""
    for key in WAIT_STATE_KEYS:
        context.user_data.pop(key, None)


def set_wait_state(context: ContextTypes.DEFAULT_TYPE, state_key: str) -> None:
    """Сбрасывает предыдущие состояния и ставит новое."""
    reset_wait_state(context)
    context.user_data[state_key] = True


# =======================
# SILENT MODE
# =======================
def is_silent_mode() -> bool:
    """True, если тихий режим включён (настройка silent_mode == '1')."""
    return db.get_setting("silent_mode") == "1"


def set_silent_mode(enable: bool) -> None:
    """Включает/выключает тихий режим (настройка silent_mode)."""
    db.set_setting("silent_mode", "1" if enable else "0")


# =======================
# VK TOKEN STATE
# =======================
def set_token_bad_state(is_bad: bool, reason: str = "") -> None:
    """Помечает VK-токен рабочим ('ok') или сломанным ('bad', причина до 400 символов)."""
    if is_bad:
        db.set_setting("token_state", "bad")
        db.set_setting("token_reason", reason[:400])
    else:
        db.set_setting("token_state", "ok")
        db.set_setting("token_reason", "")


def is_token_bad() -> bool:
    """True, если состояние токена 'bad'."""
    return db.get_setting("token_state").strip() == "bad"


def get_token_bad_reason() -> str:
    """Возвращает сохранённую причину нерабочего токена (trimmed)."""
    return db.get_setting("token_reason").strip()


async def show_main_menu(update: Update, text: str):
    """Отвечает текстом с главной клавиатурой."""
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)
