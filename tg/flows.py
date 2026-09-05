"""Диалоговые потоки Telegram: приглашения ввода и обработка значений.

ask_* и set_*_from_text перенесены дословно из bot_host.py (подзадача A2).
Вынесены в отдельный файл, чтобы tg/handlers.py оставался < 500 строк.
db-функции вызываются с явным префиксом db (в монолите — NameError Этапа 1,
здесь устранён).

Этап 3 (TikTok login): ask_tiktok_login / set_tiktok_login_from_text —
диалог «email:password» -> playwright-вход (tiktok.login) -> cookies
сохраняются шифрованно (db.save_tiktok_session). Пароль/cookies никогда
не логируются и не попадают в ответы.

VK-вход по логину/паролю живёт в tg/vk_login_flows.py (файлы держим < 500
строк); здесь только реэкспорт его публичных функций для обратной
совместимости импортов ``from tg.flows import ...``.
"""
from .vk_login_flows import (  # noqa: F401 — реэкспорт
    _clear_vk_login_ctx,
    _parse_vk_login_input,
    ask_vk_login,
    set_vk_captcha_from_text,
    set_vk_code_from_text,
    set_vk_login_from_text,
)
import asyncio
import json
import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
import db
from tiktok import check_and_send_new_tiktoks
from tiktok.login import login_tiktok
from utils import normalize_tiktok_username

from .helpers import reset_wait_state, set_token_bad_state, set_wait_state, show_main_menu
from .messages import (
    ask_tiktok_login_text,
    ask_tiktok_username_text,
    ask_vk_id_text,
    ask_vk_token_text,
    bad_tiktok_username_text,
    bad_vk_id_text,
    empty_token_text,
    tiktok_login_bad_credentials_text,
    tiktok_login_bad_format_text,
    tiktok_login_captcha_text,
    tiktok_login_disabled_text,
    tiktok_login_error_text,
    tiktok_login_ok_text,
    tiktok_login_timeout_text,
    tiktok_login_unavailable_text,
    tiktok_username_saved_text,
    vk_id_saved_text,
)

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """Маскирует email/логин: первые 2 символа + '***' (менее 2 -> '***')."""
    email = (email or "").strip()
    if len(email) < 2:
        return "***"
    return email[:2] + "***"


async def ask_vk_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ставит wait-state и просит прислать VK ID."""
    set_wait_state(context, "await_vk_id")
    await show_main_menu(update, ask_vk_id_text())


async def ask_vk_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ставит wait-state и просит прислать VK token."""
    set_wait_state(context, "await_vk_token")
    await show_main_menu(update, ask_vk_token_text())


async def ask_tiktok_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ставит wait-state и просит прислать TikTok username."""
    set_wait_state(context, "await_tiktok_username")
    await show_main_menu(update, ask_tiktok_username_text())


async def set_vk_token_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, token_text: str):
    """Сохраняет VK token (override), снимает 'bad'-состояние токена."""
    new_token = (token_text or "").strip()
    if not new_token:
        await show_main_menu(update, empty_token_text())
        return

    db.set_setting("vk_token_override", new_token)
    set_token_bad_state(False)
    reset_wait_state(context)
    await show_main_menu(update, token_saved_text())


async def set_vk_id_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, vk_id_text: str):
    """Валидирует и сохраняет VK ID пользователя."""
    text = (vk_id_text or "").strip()
    if not (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
        await show_main_menu(update, bad_vk_id_text())
        return

    tg_id = update.message.chat_id
    db.save_user_vk_id(tg_id, text)
    reset_wait_state(context)
    await show_main_menu(update, vk_id_saved_text(text))


async def set_tiktok_username_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_username: str):
    """Нормализует и сохраняет TikTok username; при смене — сброс истории и синхронизации."""
    username = normalize_tiktok_username(raw_username)
    if not username:
        await show_main_menu(update, bad_tiktok_username_text())
        return

    tg_id = update.message.chat_id
    old_username = db.get_user_tiktok_username(tg_id)

    db.save_user_tiktok_username(tg_id, username)
    if old_username != username:
        db.clear_tiktok_sent_for_user(tg_id)
        db.reset_tiktok_sync_state(tg_id)

    reset_wait_state(context)
    await show_main_menu(update, tiktok_username_saved_text(username))

    asyncio.create_task(check_and_send_new_tiktoks(context.application, tg_id, username))


async def ask_tiktok_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ставит wait-state await_tiktok_login и просит прислать email:password."""
    set_wait_state(context, "await_tiktok_login")
    await show_main_menu(update, ask_tiktok_login_text())


def _parse_login_input(text: str) -> tuple[str, str] | None:
    """Разбор строки 'email:password' -> (email, password) | None.

    Разделитель ':' первый: email не содержит ':', пароль может. Оба strip.
    Валидация: email содержит '@', пароль непустой.
    """
    if ":" not in text:
        return None
    email, password = text.split(":", 1)
    email = email.strip()
    password = password.strip()
    if "@" not in email or not password:
        return None
    return email, password


async def _handle_tiktok_login_result(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                      tg_id: int, email: str, result: dict) -> None:
    """Маппит статус login_tiktok на сообщение; секреты не выводятся никогда."""
    status = str(result.get("status") or "")

    if status == "ok":
        sessionid = str(result.get("sessionid") or "")
        cookies = result.get("cookies") or []
        if sessionid and cookies:
            db.save_tiktok_session(tg_id, sessionid, json.dumps(cookies))
            reset_wait_state(context)
            await show_main_menu(update, tiktok_login_ok_text(_mask_email(email)))
        else:
            logger.warning("TikTok login ok без sessionid/cookies tg_id=%s", tg_id)
            reset_wait_state(context)
            await show_main_menu(update, tiktok_login_error_text())
        return

    if status == "need_captcha":
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_captcha_text())
    elif status == "wrong_credentials":
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_bad_credentials_text())
    elif status == "unavailable":
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_unavailable_text())
    elif status == "timeout":
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_timeout_text())
    else:
        logger.warning("TikTok login: неизвестный статус %s tg_id=%s", status, tg_id)
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_error_text())


async def set_tiktok_login_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обрабатывает ввод 'email:password' -> сохраняет логин -> playwright-вход.

    TIKTOK_LOGIN_ENABLED=0 -> сообщение об отключённом входе. Иначе логин/
    пароль сохраняются шифрованно (db.save_tiktok_login) и запускается вход;
    таймаут int(TIKTOK_POST_TIMEOUT_SECONDS)+60. При ok cookies сохраняются
    через db.save_tiktok_session (шифрование внутри). Wait-state сбрасывается
    во всех исходах.
    """
    parsed = _parse_login_input(text or "")
    if parsed is None:
        await show_main_menu(update, tiktok_login_bad_format_text())
        return

    tg_id = update.message.chat_id

    if not getattr(config, "TIKTOK_LOGIN_ENABLED", False):
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_disabled_text())
        return

    email, password = parsed
    try:
        db.save_tiktok_login(tg_id, email, password)
    except Exception:
        logger.exception("Не удалось сохранить tiktok login tg_id=%s", tg_id)
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_error_text())
        return

    timeout_seconds = int(getattr(config, "TIKTOK_POST_TIMEOUT_SECONDS", 240) or 240) + 60
    headless = bool(getattr(config, "TIKTOK_LOGIN_HEADLESS", True))

    try:
        result = await asyncio.wait_for(
            login_tiktok(email, password, headless=headless,
                         timeout_seconds=timeout_seconds),
            timeout=timeout_seconds + 30,
        )
    except asyncio.TimeoutError:
        logger.warning("TikTok login: внешний wait_for таймаут tg_id=%s", tg_id)
        result = {"status": "timeout"}
    except Exception:
        logger.exception("TikTok login: исключение tg_id=%s", tg_id)
        reset_wait_state(context)
        await show_main_menu(update, tiktok_login_error_text())
        return

    await _handle_tiktok_login_result(update, context, tg_id, email, result)
