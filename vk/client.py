"""Клиент VK API: HTTP-запросы к api.vk.com и проверка токена на чтение сторис.

Этап 6: транспорт переведён на httpx.AsyncClient (асинхронно, без executor).
- get_http_client(): ленивый модульный singleton httpx.AsyncClient с
  timeout=config.VK_API_TIMEOUT_SECONDS и заголовком User-Agent.
- close_http_client(): идемпотентное закрытие (для post_shutdown/тестов).
- vk_call: контракт ошибок сохранён дословно ("VK error <code>: <msg>",
  "VK request failed: ..."); возврат (ok, data, err) не менялся.

vk/auth.py остаётся на requests.post (отдельный oauth-флоу, вне scope этапа).
Секреты (access_token) никогда не логируются: в лог попадают только тексты
ошибок VK без токенов.
"""
import logging

import httpx

import config
import db

logger = logging.getLogger(__name__)

# Модульный ленивый singleton HTTP-клиента (создаётся в работающем event loop).
_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Возвращает общий httpx.AsyncClient, создавая его при первом вызове.

    Повторные вызовы (в т.ч. конкурентные в одном loop) возвращают тот же
    экземпляр: asyncio-конкурентность кооперативная, гонки на создании нет.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.VK_API_TIMEOUT_SECONDS),
            headers={"User-Agent": config.USER_AGENT},
        )
    return _client


async def close_http_client() -> None:
    """Идемпотентно закрывает общий httpx.AsyncClient (безопасно вызывать повторно)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:  # pragma: no cover - защита от падения shutdown
            logger.warning("Ошибка закрытия httpx-клиента VK: %r", exc)
        _client = None


async def vk_call(method: str, params: dict):
    """Вызывает метод VK API (httpx, async); возвращает (ok, data, error_text).

    При ответе с ``error`` возвращает (False, data, "VK error <code>: <msg>"),
    при транспортной ошибке — (False, {}, "VK request failed: ...").
    """
    url = f"https://api.vk.com/method/{method}"

    try:
        client = await get_http_client()
        response = await client.get(url, params=params)
        data = response.json()

        if "error" in data:
            error_obj = data.get("error", {})
            code = error_obj.get("error_code")
            msg = error_obj.get("error_msg", "VK error")
            return False, data, f"VK error {code}: {msg}"

        return True, data, ""
    except Exception as exc:
        return False, {}, f"VK request failed: {exc!r}"


async def check_token_works_for_stories(vk_id_for_test: str | None = None):
    """Проверяет активный VK-токен: users.get + stories.get.

    Возвращает (True, "ok") либо (False, причина). Токен и его ступень (tier)
    берутся напрямую из db.get_any_active_vk_token_with_tier().

    Фикс бага №1: при tier='service' и ошибке 28 (stories.get сервисным
    токеном не работает) причина содержит понятную подсказку: пришлите
    /token или войдите через /login. Интегрируется с token_watcher
    (scheduler): уведомление только на переходе состояния.
    """
    token, tier = db.get_any_active_vk_token_with_tier()
    token = token or ""
    if not token:
        return False, "VK токен не задан"

    ok, _, err = await vk_call(
        "users.get",
        {
            "v": "5.131",
            "access_token": token,
        },
    )
    if not ok:
        return False, err

    owner_id = vk_id_for_test if vk_id_for_test else "1"
    ok2, _, err2 = await vk_call(
        "stories.get",
        {
            "v": "5.131",
            "owner_id": owner_id,
            "access_token": token,
        },
    )
    if not ok2:
        reason = err2 or "VK error"
        if tier == "service" and "VK error 28" in reason:
            reason = (
                f"{reason}: сервисный ключ приложения не может читать сторис. "
                "Пришлите /token или войдите через /login"
            )
        return False, reason

    return True, "ok"
