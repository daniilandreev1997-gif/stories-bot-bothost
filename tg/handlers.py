"""Обработчики команд и текстовых сообщений Telegram-бота.

Все обработчики перенесены дословно из bot_host.py (подзадача A2):
start_cmd, silent_cmd, list_cmd, who_cmd, checknow_cmd, token_cmd,
cleartoken_cmd, tiktok_cmd, tiktokreset_cmd, handle_text. Тексты сообщений
взяты из tg.messages (дословно те же строки), бизнес-вызовы — из vk и
tiktok. db-функции вызываются с явным префиксом db (устранение NameError
Этапа 1).
"""
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

import db
from tiktok import check_and_send_new_tiktoks
from utils import normalize_tiktok_username
from vk import check_token_works_for_stories, checknow_send_all_vk

from .flows import (
    ask_tiktok_login,
    ask_tiktok_username,
    ask_vk_id,
    ask_vk_token,
    set_tiktok_login_from_text,
    set_tiktok_username_from_text,
    set_vk_id_from_text,
    set_vk_token_from_text,
)
from .helpers import (
    is_silent_mode,
    is_token_bad,
    reset_wait_state,
    set_silent_mode,
    set_token_bad_state,
    show_main_menu,
)
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
)
from .messages import (
    checking_tiktok_text,
    checking_vk_text,
    done_text,
    empty_text_message,
    help_text,
    list_header,
    list_lines,
    no_targets_text,
    no_tiktok_username_text,
    no_vk_id_text,
    silent_off_status,
    silent_on_status,
    silent_status_text,
    token_cleared_text,
    token_ok_text,
    token_problem_text,
    tiktok_reset_text,
    unknown_input_text,
)

logger = logging.getLogger(__name__)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start: сброс wait-state и справка по кнопкам/командам."""
    reset_wait_state(context)
    await show_main_menu(update, help_text())


async def silent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /silent: переключает тихий режим."""
    reset_wait_state(context)
    new_state = not is_silent_mode()
    set_silent_mode(new_state)

    status = silent_on_status() if new_state else silent_off_status()
    await show_main_menu(update, silent_status_text(status))


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list: показывает, кого мониторим и состояние VK-токена."""
    reset_wait_state(context)
    tg_id = update.message.chat_id

    vk_id = db.get_user_vk_id(tg_id)
    tiktok_username = db.get_user_tiktok_username(tg_id)

    token_state = "плохой" if is_token_bad() else "ok"
    lines = list_lines(vk_id, tiktok_username, token_state)

    await show_main_menu(update, list_header() + "\n".join(lines))


async def who_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /who: проверяет работоспособность VK-токена на текущем VK ID."""
    reset_wait_state(context)
    tg_id = update.message.chat_id
    vk_id = db.get_user_vk_id(tg_id)

    if not vk_id:
        await show_main_menu(update, no_vk_id_text())
        return

    ok, reason = await check_token_works_for_stories(vk_id)
    if ok:
        await show_main_menu(update, token_ok_text())
    else:
        await show_main_menu(update, token_problem_text(reason))


async def checknow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /checknow: немедленная проверка VK и/или TikTok."""
    reset_wait_state(context)
    tg_id = update.message.chat_id

    vk_id = db.get_user_vk_id(tg_id)
    tiktok_username = db.get_user_tiktok_username(tg_id)

    if not vk_id and not tiktok_username:
        await show_main_menu(update, no_targets_text())
        return

    if vk_id:
        await show_main_menu(update, checking_vk_text())
        await checknow_send_all_vk(context.application, tg_id, vk_id)

    if tiktok_username:
        await show_main_menu(update, checking_tiktok_text(tiktok_username))
        await check_and_send_new_tiktoks(context.application, tg_id, tiktok_username)

    await show_main_menu(update, done_text())


async def token_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /token: токен из аргументов либо приглашение ввода."""
    args = context.args
    if not args:
        await ask_vk_token(update, context)
        return

    await set_vk_token_from_text(update, context, " ".join(args))


async def cleartoken_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /cleartoken: сброс override-токена и 'bad'-состояния."""
    reset_wait_state(context)
    db.set_setting("vk_token_override", "")
    set_token_bad_state(False)
    await show_main_menu(update, token_cleared_text())


async def tiktok_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /tiktok: username из аргументов либо приглашение ввода."""
    args = context.args
    if not args:
        await ask_tiktok_username(update, context)
        return

    await set_tiktok_username_from_text(update, context, " ".join(args))


async def tiktokreset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /tiktokreset: сброс истории TikTok и повторная загрузка."""
    reset_wait_state(context)
    tg_id = update.message.chat_id
    username = db.get_user_tiktok_username(tg_id)

    if not username:
        await show_main_menu(update, no_tiktok_username_text())
        return

    db.clear_tiktok_sent_for_user(tg_id)
    db.reset_tiktok_sync_state(tg_id)
    await show_main_menu(update, tiktok_reset_text(username))
    asyncio.create_task(check_and_send_new_tiktoks(context.application, tg_id, username))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Маршрутизатор текстовых сообщений: кнопки -> wait-state -> fallback-эвристика."""
    text = (update.message.text or "").strip()

    if not text:
        await show_main_menu(update, empty_text_message())
        return

    # Buttons
    if text == BUTTON_VK_ID:
        await ask_vk_id(update, context)
        return

    if text == BUTTON_TIKTOK:
        await ask_tiktok_username(update, context)
        return

    if text == BUTTON_TOKEN_VK:
        await ask_vk_token(update, context)
        return

    if text == BUTTON_CHECK_NOW:
        await checknow_cmd(update, context)
        return

    if text == BUTTON_LIST:
        await list_cmd(update, context)
        return

    if text == BUTTON_TIKTOK_RESET:
        await tiktokreset_cmd(update, context)
        return

    if text == BUTTON_SILENT:
        await silent_cmd(update, context)
        return

    if text == BUTTON_CLEAR_TOKEN:
        await cleartoken_cmd(update, context)
        return

    if text == BUTTON_TIKTOK_LOGIN:
        await ask_tiktok_login(update, context)
        return

    # Awaited states
    if context.user_data.get("await_vk_id"):
        await set_vk_id_from_text(update, context, text)
        return

    if context.user_data.get("await_vk_token"):
        await set_vk_token_from_text(update, context, text)
        return

    if context.user_data.get("await_tiktok_username"):
        await set_tiktok_username_from_text(update, context, text)
        return

    if context.user_data.get("await_tiktok_login"):
        await set_tiktok_login_from_text(update, context, text)
        return

    # Backward-compatible free text behavior
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        await set_vk_id_from_text(update, context, text)
        return

    possible_username = normalize_tiktok_username(text)
    if possible_username:
        await set_tiktok_username_from_text(update, context, possible_username)
        return

    await show_main_menu(update, unknown_input_text())
