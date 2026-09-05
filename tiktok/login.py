"""Клиент входа TikTok по логину/паролю через playwright (чистый, без db/telegram).

Паттерн vk/auth.py: чистый клиент, структурированные результаты-статусы,
секреты (пароль/cookies) никогда не логируются. Зависимость playwright
ОПЦИОНАЛЬНА: если библиотека не установлена — статус "unavailable", без
исключений.

Статусы результата login_tiktok:
- "ok"               — вход выполнен, sessionid + cookies собраны;
- "need_captcha"     — TikTok показал капчу/слайдер;
- "wrong_credentials" — неверный логин/пароль (сообщение об ошибке в DOM);
- "timeout"          — бюджет входа исчерпан без определяемого результата;
- "unavailable"      — playwright не установлен.

cookies_to_netscape / cookies_json_to_netscape / save_cookies_to_temp —
чистые конвертеры в Netscape-формат для yt-dlp cookiefile.
"""
import asyncio
import json
import logging
import tempfile

import config

logger = logging.getLogger(__name__)

try:  # playwright — опциональная зависимость (не в requirements.txt).
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:  # ImportError и любые проблемы загрузки браузерного пакета.
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False

TIKTOK_LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email"
TIKTOK_DOMAIN_SUFFIX = "tiktok.com"

# TikTok меняет DOM: везде списки альтернативных селекторов, первый найденный.
USERNAME_SELECTORS = (
    'input[name="username"]',
    'input[type="text"]',
)
PASSWORD_SELECTORS = (
    'input[type="password"]',
)
SUBMIT_SELECTORS = (
    'button[type="submit"]',
    '[data-e2e="login-button"]',
)
CAPTCHA_SELECTORS = (
    '[data-e2e="captcha-verify-container"]',
    'iframe[src*="captcha"]',
    ".captcha-verify-container",
    "#captcha_container",
    '[class*="captcha"]',
)
ERROR_SELECTORS = (
    '[data-e2e="login-error"]',
)
ERROR_TEXT_MARKERS = (
    "incorrect account or password",
    "неверный логин или пароль",
    "неверный пароль",
    "wrong password",
)

# Поля cookies, которые сохраняются/конвертируются (без лишних служебных).
_COOKIE_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def _first_visible(page, selectors: tuple[str, ...], timeout_ms: int):
    """Первый видимый элемент по списку селекторов; None если ни один не найден.

    Короткий таймаут на каждый селектор (TikTok меняет DOM — перебор обязателен).
    """
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            continue
    return None


def _extract_cookies(cookies: list[dict]) -> list[dict]:
    """Проекция cookies до разрешённых полей (_COOKIE_FIELDS), без лишнего."""
    result: list[dict] = []
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        projected = {field: cookie.get(field) for field in _COOKIE_FIELDS}
        result.append(projected)
    return result


def _has_tiktok_session_cookie(cookies: list[dict]) -> str:
    """sessionid/sessionid_ss с доменом *tiktok.com; '' если нет."""
    for cookie in cookies or []:
        name = str(cookie.get("name") or "")
        domain = str(cookie.get("domain") or "")
        if name in ("sessionid", "sessionid_ss") and TIKTOK_DOMAIN_SUFFIX in domain:
            return name
    return ""


async def login_tiktok(
    email: str,
    password: str,
    *,
    headless: bool = True,
    timeout_seconds: int = 120,
) -> dict:
    """Вход TikTok по email/username+пароль через playwright (async).

    Args:
        email: email или username TikTok. Не логируется.
        password: пароль. Не логируется.
        headless: запуск браузера без окна (config.TIKTOK_LOGIN_HEADLESS).
        timeout_seconds: общий бюджет входа (goto + результат).

    Returns:
        Статусные словари (секреты не включаются, cookies — только при ok):
        - {"status": "ok", "sessionid": str, "cookies": [ {name, value, domain,
          path, expires, httpOnly, secure, sameSite}, ... ]}
        - {"status": "need_captcha", "reason": str}
        - {"status": "wrong_credentials"}
        - {"status": "timeout"}
        - {"status": "unavailable", "reason": "playwright not installed"}
    """
    if not PLAYWRIGHT_AVAILABLE:
        logger.warning("TikTok login: playwright не установлен, вход невозможен")
        return {"status": "unavailable", "reason": "playwright not installed"}

    budget_ms = max(1, int(timeout_seconds)) * 1000
    step_ms = 3000  # короткий таймаут на каждый селектор (DOM TikTok нестабилен).

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                deadline = asyncio.get_running_loop().time() + float(timeout_seconds)

                try:
                    await page.goto(
                        TIKTOK_LOGIN_URL,
                        wait_until="domcontentloaded",
                        timeout=min(budget_ms, timeout_seconds * 1000),
                    )
                except Exception as exc:
                    logger.warning("TikTok login: goto failed (%s)", type(exc).__name__)
                    return {"status": "timeout"}

                # --- Заполнение формы (списки альтернативных селекторов) ---
                username_input = _first_visible(page, USERNAME_SELECTORS, step_ms)
                if username_input is None:
                    return {"status": "timeout"}
                await username_input.fill(email)

                password_input = _first_visible(page, PASSWORD_SELECTORS, step_ms)
                if password_input is None:
                    return {"status": "timeout"}
                await password_input.fill(password)

                submit = _first_visible(page, SUBMIT_SELECTORS, step_ms)
                if submit is not None:
                    await submit.click()

                # --- Ожидание результата до общего бюджета ---
                while asyncio.get_running_loop().time() < deadline:
                    cookies = await context.cookies()

                    session_name = _has_tiktok_session_cookie(cookies)
                    url = page.url or ""
                    url_ok = bool(url) and "/login" not in url and TIKTOK_DOMAIN_SUFFIX in url

                    if session_name or url_ok:
                        sessionid = ""
                        for cookie in cookies:
                            if str(cookie.get("name") or "") == "sessionid":
                                sessionid = str(cookie.get("value") or "")
                                break
                        projected = _extract_cookies(cookies)
                        logger.info("TikTok login OK (cookies count=%d)", len(projected))
                        return {"status": "ok", "sessionid": sessionid, "cookies": projected}

                    for selector in CAPTCHA_SELECTORS:
                        try:
                            if await page.locator(selector).first.count() > 0:
                                logger.warning("TikTok login: need_captcha")
                                return {
                                    "status": "need_captcha",
                                    "reason": (
                                        "tiktok showed captcha/slider; "
                                        "повтори позже или вставь cookies вручную"
                                    ),
                                }
                        except Exception:
                            continue

                    for selector in ERROR_SELECTORS:
                        try:
                            locator = page.locator(selector).first
                            if await locator.count() > 0 and await locator.is_visible():
                                logger.warning("TikTok login: wrong_credentials (error selector)")
                                return {"status": "wrong_credentials"}
                        except Exception:
                            continue

                    try:
                        page_text = (await page.content()).lower()
                    except Exception:
                        page_text = ""
                    if any(marker in page_text for marker in ERROR_TEXT_MARKERS):
                        logger.warning("TikTok login: wrong_credentials (text marker)")
                        return {"status": "wrong_credentials"}

                    await asyncio.sleep(1.0)

                logger.warning("TikTok login: timeout")
                return {"status": "timeout"}
            finally:
                await browser.close()
    except Exception:
        logger.exception("TikTok login: внутренняя ошибка playwright")
        return {"status": "timeout"}


def cookies_to_netscape(cookies: list[dict]) -> str:
    """Конвертирует список cookies (dict) в Netscape HTTP Cookie File (LF).

    Формат строки: domain \t flag(TRUE) \t path \t secure \t expiration \t name \t value.
    Отсутствующий/сессионный (0) expires -> 0. Заголовок "# HTTP Cookie File".
    """
    lines = ["# HTTP Cookie File"]
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name:
            continue
        domain = str(cookie.get("domain") or "") or ".tiktok.com"
        path = str(cookie.get("path") or "/") or "/"
        secure = bool(cookie.get("secure"))
        try:
            expires = int(cookie.get("expires") or 0)
        except Exception:
            expires = 0
        lines.append(
            "\t".join([domain, "TRUE", path, "TRUE" if secure else "FALSE",
                       str(expires), name, value])
        )
    return "\n".join(lines) + "\n"


def cookies_json_to_netscape(cookies_json: str) -> str | None:
    """cookies_json (JSON-список dict из save_tiktok_session) -> Netscape-текст.

    Повреждённый JSON/не-список -> None (вызывающая сторона падает на fallback).
    """
    if not cookies_json:
        return None
    try:
        data = json.loads(cookies_json)
    except Exception:
        logger.warning("cookies_json_to_netscape: невалидный JSON")
        return None
    if not isinstance(data, list):
        logger.warning("cookies_json_to_netscape: JSON не список")
        return None
    return cookies_to_netscape(data)


def save_cookies_to_temp(netscape_text: str) -> str:
    """Пишет Netscape-текст во временный файл; возвращает путь (удалает вызывающий)."""
    handle = tempfile.NamedTemporaryFile(
        prefix="tiktok_cookies_", suffix=".txt", delete=False, mode="w", encoding="utf-8"
    )
    try:
        handle.write(netscape_text)
    finally:
        handle.close()
    return handle.name


def login_tiktok_sync(email: str, password: str, *, headless: bool = True,
                      timeout_seconds: int = 120) -> dict:
    """Синхронная обёртка над login_tiktok (asyncio.run); для не-async вызова."""
    return asyncio.run(login_tiktok(email, password, headless=headless,
                                    timeout_seconds=timeout_seconds))
