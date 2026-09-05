"""VK-вход по логину/паролю: диалоговый флоу поверх vk.auth.vk_direct_auth.

По образцу TikTok-блока tg/flows.py (Этап 3), вынесен в отдельный файл, чтобы
tg/flows.py и этот модуль оставались < 500 строк.

Контракт (docs/design-vk-login.md):
- пользователь присылает "логин:пароль" одной строкой; разделитель — ПЕРВОЕ ':';
- need_validation -> запрос кода 2FA -> повторный вызов с code=...;
- need_captcha -> отправка картинки капчи -> повторный вызов с captcha_sid/captcha_key;
- ok -> db.save_vk_user_token (per-user, шифрование внутри db) + опциональный
  post-check stories.get (warning при фейле, НЕ блокирует сохранение).

Секреты (login/password/token/code) никогда не логируются и не попадают в
тексты сообщений — правило vk/auth.py распространено на слой tg.
"""
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
import db
from vk.auth import vk_direct_auth
from vk.client import vk_call

from .helpers import reset_wait_state, set_wait_state, show_main_menu
from .messages import (
    vk_bad_password_text,
    vk_login_bad_format_text,
    vk_login_disabled_text,
    vk_login_error_text,
    vk_login_intro_text,
    vk_login_ok_text,
    vk_login_ok_with_warning_text,
    vk_need_captcha_text,
    vk_need_code_text,
    vk_network_error_retry_text,
    vk_too_much_tries_text,
    vk_wrong_otp_text,
)

logger = logging.getLogger(__name__)

VK_LOGIN_CTX_KEY = "vk_login_ctx"

STATE_AWAIT_VK_LOGIN = "await_vk_login"
STATE_AWAIT_VK_CODE = "await_vk_code"
STATE_AWAIT_VK_CAPTCHA = "await_vk_captcha"

VK_CODE_MAX_ATTEMPTS = 3
VK_CAPTCHA_MAX_ATTEMPTS = 2


def _parse_vk_login_input(text: str) -> tuple[str, str] | None:
    """Разбор строки 'login:password' -> (login, password) | None.

    Отличия от TikTok-парсера: '@' НЕ требуется (VK-логин — телефон или email),
    регистр сохраняется. Разделитель — первое ':': логин не содержит ':',
    пароль может. Обе части strip(); валидно, если обе непустые.
    """
    text = (text or "").strip()
    if ":" not in text:
        return None
    login, password = text.split(":", 1)
    login = login.strip()
    password = password.strip()
    if not login or not password:
        return None
    return login, password


def _clear_vk_login_ctx(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбрасывает tmp-контекст диалога и wait-state (пароль умирает с user_data)."""
    context.user_data.pop(VK_LOGIN_CTX_KEY, None)
    reset_wait_state(context)


def _get_vk_login_ctx(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Возвращает tmp-контекст диалога или пустой dict (не создаёт запись)."""
    ctx = context.user_data.get(VK_LOGIN_CTX_KEY)
    return ctx if isinstance(ctx, dict) else {}


async def _post_check_stories(user_id: str, access_token: str) -> str:
    """Проверяет, что новый токен умеет читать сторис (прямой vk_call).

    Возвращает '' при успехе либо короткую причину для warning-текста.
    Транспортная ошибка трактуется как предупреждение, не как фейл входа.
    """
    if not user_id:
        return "VK не вернул user_id для проверки"
    try:
        ok, _, err = await vk_call(
            "stories.get",
            {"v": "5.131", "owner_id": user_id, "access_token": access_token},
        )
    except Exception as exc:  # pragma: no cover - защита от падения post-check
        logger.warning("VK login post-check: исключение (%s)", type(exc).__name__)
        return "проверка недоступна (сеть)"
    if ok:
        return ""
    short = (err or "").strip()
    return short[:120] if short else "VK отклонил запрос сторис"


async def _vk_auth_call(update: Update, context: ContextTypes.DEFAULT_TYPE, tg_id: int) -> None:
    """Единая точка вызова vk_direct_auth с tmp-контекстом и маппингом исходов.

    ctx может содержать code/captcha_sid/captcha_key — тогда это повторный
    вызов после need_validation/need_captcha. Таймаут: VK_API_TIMEOUT_SECONDS
    (внутри vk_direct_auth) + запас на executor.
    """
    ctx = _get_vk_login_ctx(context)
    login = str(ctx.get("login") or "")
    password = str(ctx.get("password") or "")
    timeout_seconds = int(getattr(config, "VK_API_TIMEOUT_SECONDS", 20) or 20) + 60

    try:
        result = await asyncio.wait_for(
            vk_direct_auth(
                login,
                password,
                client_id=config.VK_DIRECT_AUTH_CLIENT_ID,
                client_secret=config.VK_DIRECT_AUTH_CLIENT_SECRET,
                scope=config.VK_DIRECT_AUTH_SCOPE,
                code=ctx.get("code"),
                captcha_sid=ctx.get("captcha_sid"),
                captcha_key=ctx.get("captcha_key"),
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("VK login: внешний wait_for таймаут tg_id=%s", tg_id)
        result = {"ok": False, "error": "network_error"}
    except Exception:
        logger.exception("VK login: исключение вызова vk_direct_auth tg_id=%s", tg_id)
        result = {"ok": False, "error": "unknown_error"}

    await _handle_vk_auth_result(update, context, tg_id, result)


async def _handle_vk_auth_result(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 tg_id: int, result: dict) -> None:
    """Маппит статус vk_direct_auth на действие/сообщение (таблица §5.2 дока).

    Секреты не выводятся: в текстах только phone_mask (от VK) и статусы ошибок.
    """
    status = str(result.get("error") or "")
    if result.get("ok"):
        status = "ok"

    if status == "ok":
        access_token = str(result.get("access_token") or "")
        user_id = str(result.get("user_id") or "")
        if not access_token:
            logger.warning("VK login ok без access_token tg_id=%s", tg_id)
            _clear_vk_login_ctx(context)
            await show_main_menu(update, vk_login_error_text())
            return

        if getattr(config, "VK_STORE_PASSWORD", False):
            try:
                ctx = _get_vk_login_ctx(context)
                db.save_vk_user_password(tg_id, str(ctx.get("login") or ""),
                                         str(ctx.get("password") or ""))
            except Exception:
                # Парольная запись опциональна: фейл не должен ломать вход.
                logger.warning("VK login: save_vk_user_password не удался tg_id=%s", tg_id)

        try:
            db.save_vk_user_token(tg_id, access_token)
        except Exception:
            logger.exception("VK login: не удалось сохранить токен tg_id=%s", tg_id)
            _clear_vk_login_ctx(context)
            await show_main_menu(update, vk_login_error_text())
            return

        warning = await _post_check_stories(user_id, access_token)
        _clear_vk_login_ctx(context)
        if warning:
            logger.warning("VK login post-check: warning для tg_id=%s (причина не секретна)", tg_id)
            await show_main_menu(update, vk_login_ok_with_warning_text(warning))
        else:
            await show_main_menu(update, vk_login_ok_text())
        return

    if status == "need_validation":
        ctx = _get_vk_login_ctx(context)
        ctx["validation_sid"] = str(result.get("validation_sid") or "")
        ctx["phone_mask"] = str(result.get("phone_mask") or "")
        context.user_data[VK_LOGIN_CTX_KEY] = ctx
        reset_wait_state(context)
        set_wait_state(context, STATE_AWAIT_VK_CODE)
        await show_main_menu(update, vk_need_code_text(ctx["phone_mask"]))
        return

    if status == "need_captcha":
        ctx = _get_vk_login_ctx(context)
        ctx["captcha_sid"] = str(result.get("captcha_sid") or "")
        ctx["attempts_captcha"] = int(ctx.get("attempts_captcha") or 0) + 1
        context.user_data[VK_LOGIN_CTX_KEY] = ctx
        if ctx["attempts_captcha"] > VK_CAPTCHA_MAX_ATTEMPTS:
            logger.warning("VK login: лимит попыток капчи tg_id=%s", tg_id)
            _clear_vk_login_ctx(context)
            await show_main_menu(update, vk_too_much_tries_text())
            return
        set_wait_state(context, STATE_AWAIT_VK_CAPTCHA)
        captcha_img = str(result.get("captcha_img") or "")
        if captcha_img:
            try:
                await update.message.reply_photo(photo=captcha_img)
            except Exception:
                logger.warning("VK login: не удалось отправить фото капчи tg_id=%s", tg_id)
        await show_main_menu(update, vk_need_captcha_text())
        return

    if status == "wrong_otp":
        ctx = _get_vk_login_ctx(context)
        attempts = int(ctx.get("attempts_code") or 0) + 1
        ctx["attempts_code"] = attempts
        context.user_data[VK_LOGIN_CTX_KEY] = ctx
        if attempts >= VK_CODE_MAX_ATTEMPTS:
            logger.warning("VK login: лимит неверных кодов tg_id=%s", tg_id)
            _clear_vk_login_ctx(context)
            await show_main_menu(update, vk_too_much_tries_text())
            return
        set_wait_state(context, STATE_AWAIT_VK_CODE)
        await show_main_menu(update, vk_wrong_otp_text(VK_CODE_MAX_ATTEMPTS - attempts))
        return

    if status == "bad_password":
        logger.warning("VK login: bad_password tg_id=%s", tg_id)
        _clear_vk_login_ctx(context)
        await show_main_menu(update, vk_bad_password_text())
        return

    if status == "too_much_tries":
        logger.warning("VK login: too_much_tries tg_id=%s", tg_id)
        _clear_vk_login_ctx(context)
        await show_main_menu(update, vk_too_much_tries_text())
        return

    if status == "network_error":
        # Состояние и ctx НЕ сбрасываются: пользователь может прислать код/капчу
        # или строку логина заново; /start отменяет диалог.
        logger.warning("VK login: network_error tg_id=%s (состояние сохранено)", tg_id)
        await show_main_menu(update, vk_network_error_retry_text())
        return

    logger.warning("VK login: неизвестный статус %s tg_id=%s", status or "unknown", tg_id)
    _clear_vk_login_ctx(context)
    await show_main_menu(update, vk_login_error_text())


async def ask_vk_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Гейт VK_LOGIN_ENABLED; ставит wait-state и просит прислать login:password."""
    if not getattr(config, "VK_LOGIN_ENABLED", False):
        await show_main_menu(update, vk_login_disabled_text())
        return

    set_wait_state(context, STATE_AWAIT_VK_LOGIN)
    await show_main_menu(update, vk_login_intro_text())


async def set_vk_login_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обрабатывает ввод 'login:password' -> tmp-контекст -> vk_direct_auth.

    Невалидный формат: текст ошибки, состояние СОХРАНЯЕТСЯ (можно прислать
    строку ещё раз). Пароль живёт только в RAM user_data.
    """
    parsed = _parse_vk_login_input(text or "")
    if parsed is None:
        await show_main_menu(update, vk_login_bad_format_text())
        return

    tg_id = update.message.chat_id
    login, password = parsed
    context.user_data[VK_LOGIN_CTX_KEY] = {
        "login": login,
        "password": password,
        "attempts_code": 0,
    }

    await _vk_auth_call(update, context, tg_id)


async def set_vk_code_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обрабатывает код 2FA -> повторный вызов vk_direct_auth с code=..."""
    tg_id = update.message.chat_id
    ctx = _get_vk_login_ctx(context)
    if not ctx.get("login"):
        _clear_vk_login_ctx(context)
        await show_main_menu(update, vk_login_error_text())
        return

    ctx["code"] = (text or "").strip()
    context.user_data[VK_LOGIN_CTX_KEY] = ctx

    await _vk_auth_call(update, context, tg_id)


async def set_vk_captcha_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Обрабатывает текст капчи -> повторный вызов с captcha_sid/captcha_key.

    Текст капчи передаётся как введён (без смены регистра).
    """
    tg_id = update.message.chat_id
    ctx = _get_vk_login_ctx(context)
    if not ctx.get("login") or not ctx.get("captcha_sid"):
        _clear_vk_login_ctx(context)
        await show_main_menu(update, vk_login_error_text())
        return

    ctx["captcha_key"] = text or ""
    context.user_data[VK_LOGIN_CTX_KEY] = ctx

    await _vk_auth_call(update, context, tg_id)
