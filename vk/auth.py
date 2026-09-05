"""Клиент прямой авторизации VK по логину/паролю (OAuth direct, grant_type=password).

Новый API-клиент Этапа 2 (запрошено пользователем). Функция vk_direct_auth
чистая: не зависит от db и Telegram, принимает готовые логин/пароль и
возвращает словарь-результат — тестируется подменой requests.

Диалоги Telegram для ввода кода 2FA — следующий этап; здесь только API.

Логирование: ТОЛЬКО коды ошибок и маска телефона. Логин, пароль,
client_secret и access_token никогда не логируются и не попадают в тексты
исключений.
"""
import asyncio
import functools
import logging

import requests

import config

logger = logging.getLogger(__name__)

VK_OAUTH_TOKEN_URL = "https://oauth.vk.com/token"
VK_API_VERSION = "5.131"


async def vk_direct_auth(
    login: str,
    password: str,
    *,
    client_id: str = config.VK_DIRECT_AUTH_CLIENT_ID,
    client_secret: str = config.VK_DIRECT_AUTH_CLIENT_SECRET,
    scope: str = config.VK_DIRECT_AUTH_SCOPE,
    code: str | None = None,
    captcha_sid: str | None = None,
    captcha_key: str | None = None,
) -> dict:
    """Прямая авторизация VK (grant_type=password) с поддержкой 2FA и капчи.

    Args:
        login: логин VK (телефон/email). Не логируется.
        password: пароль VK. Не логируется.
        client_id: ID приложения VK (env VK_DIRECT_AUTH_CLIENT_ID).
        client_secret: защищённый ключ приложения (env VK_DIRECT_AUTH_CLIENT_SECRET).
        scope: запрашиваемые права (env VK_DIRECT_AUTH_SCOPE, по умолчанию 'stories').
        code: код 2FA при повторном вызове после need_validation.
        captcha_sid: sid капчи при повторном вызове после need_captcha.
        captcha_key: ответ капчи от пользователя.

    Returns:
        Успех: ``{"ok": True, "access_token": str, "user_id": str}``.
        Ошибка: ``{"ok": False, "error": str, "code": int | None, ...}``, где error:
          - "bad_password"    (code=401)  — неверный логин/пароль;
          - "too_much_tries"  (code=406)  — слишком много попыток;
          - "need_validation" (code=None) — требуется 2FA: в ответе validation_type/
            validation_sid/phone_mask; повторить вызов с code=...;
          - "need_captcha"    (code=17)   — требуется капча: в ответе captcha_sid/
            captcha_img; повторить вызов с captcha_sid/captcha_key;
          - "wrong_otp"       (code=401)  — неверный код 2FA (при переданном code);
          - "network_error"               — транспортная ошибка запроса.
        Дополнительно всегда присутствует "error_desc" (человекочитаемое описание
        от VK, без секретов).
    """
    params = {
        "grant_type": "password",
        "client_id": client_id,
        "client_secret": client_secret,
        "username": login,
        "password": password,
        "scope": scope,
        "v": VK_API_VERSION,
        "2-factor-supported": "1",
    }
    if code:
        params["code"] = code
    if captcha_sid and captcha_key:
        params["captcha_sid"] = captcha_sid
        params["captcha_key"] = captcha_key

    loop = asyncio.get_running_loop()
    func = functools.partial(
        requests.post, VK_OAUTH_TOKEN_URL, data=params, timeout=config.VK_API_TIMEOUT_SECONDS
    )
    try:
        response = await loop.run_in_executor(None, func)
    except Exception as exc:
        logger.warning("VK direct auth: transport error (%s)", type(exc).__name__)
        return {
            "ok": False,
            "error": "network_error",
            "code": None,
            "error_desc": f"VK auth request failed: {type(exc).__name__}",
        }

    try:
        data = response.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    access_token = str(data.get("access_token") or "")
    if access_token:
        user_id = str(data.get("user_id") or "")
        logger.info("VK direct auth OK | user_id=%s", user_id)
        return {"ok": True, "access_token": access_token, "user_id": user_id}

    error = str(data.get("error") or "").strip().lower()
    error_description = str(data.get("error_description") or "").strip()
    status_code = response.status_code

    validation_type = str(data.get("validation_type") or "").strip()
    validation_sid = str(data.get("validation_sid") or "").strip()
    phone_mask = str(data.get("phone_mask") or "").strip()
    captcha_sid_resp = str(data.get("captcha_sid") or "").strip()
    captcha_img = str(data.get("captcha_img") or "").strip()

    # Требуется капча (VK error 17 -> need_captcha).
    if error == "need_captcha" or captcha_sid_resp:
        logger.warning("VK direct auth: need_captcha")
        return {
            "ok": False,
            "error": "need_captcha",
            "code": 17,
            "need_captcha": True,
            "captcha_sid": captcha_sid_resp,
            "captcha_img": captcha_img,
            "error_desc": error_description,
        }

    # Требуется 2FA-подтверждение (первый вызов без code).
    if error_description.lower() == "need_validation" or validation_sid:
        logger.warning(
            "VK direct auth: need_validation | type=%s | phone=%s",
            validation_type or "unknown",
            phone_mask or "unknown",
        )
        return {
            "ok": False,
            "error": "need_validation",
            "code": None,
            "need_validation": True,
            "validation_type": validation_type,
            "validation_sid": validation_sid,
            "phone_mask": phone_mask,
            "error_desc": error_description,
        }

    # Неверный код 2FA: при переданном code ошибка клиент-гранта трактуется как wrong_otp.
    if code and error in ("invalid_client", "invalid_grant", "invalid_request"):
        logger.warning("VK direct auth: wrong_otp")
        return {
            "ok": False,
            "error": "wrong_otp",
            "code": 401,
            "error_desc": error_description,
        }

    # Неверный логин/пароль (VK invalid_client либо HTTP 401).
    if error == "invalid_client" or status_code == 401:
        logger.warning("VK direct auth: bad_password")
        return {
            "ok": False,
            "error": "bad_password",
            "code": 401,
            "error_desc": error_description,
        }

    # Слишком много попыток (VK too_much_tries либо HTTP 406).
    if error == "too_much_tries" or status_code == 406:
        logger.warning("VK direct auth: too_much_tries")
        return {
            "ok": False,
            "error": "too_much_tries",
            "code": 406,
            "error_desc": error_description,
        }

    # Прочие ошибки VK OAuth.
    logger.warning("VK direct auth: unexpected error=%s status=%s", error or "unknown", status_code)
    return {
        "ok": False,
        "error": error or "unknown_error",
        "code": status_code or None,
        "error_desc": error_description,
    }
