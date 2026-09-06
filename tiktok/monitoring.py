"""Мониторинг TikTok: claim → доставка → mark, цикл проверки новых постов.

Этап 3 (исправление TikTok) — главная перестройка:
- атомарный claim ДО отправки (db.claim_tiktok_post, UPSERT под DB_LOCK) —
  параллельные проходы не отправят пост дважды;
- статусы доставки: media→'sent', partial→'partial', fallback→'fallback',
  failed/timeout/exception→'failed' (failed перезабирается после cooldown);
- partial delivery: если expected_count > len(files) — отправляем что есть
  с пометкой «Часть медиа недоступна»;
- retry скачивания: 1 повторная попытка с экспоненциальным backoff
  (base→max из config), после исчерпания — fallback-ссылка с причиной;
- budget цикла: deadline = time.monotonic() + TIKTOK_CYCLE_TIMEOUT_SECONDS;
  неклеймленные посты остаются на следующий проход;
- один неудачный пост не блокирует цикл (try/except на каждый пост);
- единое structured-событие tiktok_delivery на пост (machine-parseable).

Контракт владения tmp_dir (фикс бага №2: каталог удалялся до отправки):
- send_tiktok_post — владелец tmp_dir: блок отправки в try/finally, в finally
  shutil.rmtree(result["tmp_dir"], ignore_errors=True) — покрывает все исходы
  (media/partial/fallback/failed/exception) и медиагруппы;
- check_and_send_new_tiktoks вызывает sweep_stale_tiktok_tmp_dirs в начале
  цикла (подчистка «осиротевших» каталогов после таймаутов скачивания).
"""
import asyncio
import logging
import os
import shutil
import time

from telegram import InputMediaPhoto, InputMediaVideo
from telegram.ext import Application

import config
import db
from utils import chunk_media_groups, resolve_tiktok_cookiefile, safe_int

from .download import (
    build_tiktok_caption,
    download_tiktok_post,
    sweep_stale_tiktok_tmp_dirs,
)
from .extract import get_tiktok_posts
from .login import cookies_json_to_netscape, save_cookies_to_temp

logger = logging.getLogger(__name__)

# Маппинг результата доставки на статус claim в БД.
_DELIVERY_TO_STATUS = {
    "media": "sent",
    "partial": "partial",
    "fallback": "fallback",
    "failed": "failed",
}


def _post_deadline_exceeded(deadline: float | None) -> bool:
    """True, если бюджет цикла исчерпан (deadline из time.monotonic())."""
    return deadline is not None and time.monotonic() > deadline


def _short_reason(reason: str, limit: int = 120) -> str:
    """Первые ``limit`` символов причины (одной строкой) для fallback-текста."""
    text = " ".join(str(reason or "").split())
    return text[:limit]


def _is_partial(kind: str, files_count: int, expected_count: int) -> bool:
    """Эвристика partial-доставки photo-постов.

    True только для 'photos', когда ожидаемое число файлов известно (>0),
    а скачалось меньше. Video-посты и expected_count=0 не считаются partial.
    """
    return kind == "photos" and expected_count > 0 and files_count < expected_count


async def send_tiktok_media_group(app: Application, tg_id: int, files: list[str], caption: str, kind: str) -> None:
    """Отправляет медиагруппу последовательными Telegram-чанками."""
    chunks = chunk_media_groups(files, max_items=config.TG_MEDIA_GROUP_MAX_ITEMS)
    for chunk_index, chunk in enumerate(chunks):
        handles = [open(path, "rb") for path in chunk]
        media = []
        try:
            for idx, handle in enumerate(handles):
                if chunk_index == 0 and idx == 0:
                    if kind == "video":
                        media.append(InputMediaVideo(media=handle, caption=caption))
                    else:
                        media.append(InputMediaPhoto(media=handle, caption=caption))
                else:
                    if kind == "video":
                        media.append(InputMediaVideo(media=handle))
                    else:
                        media.append(InputMediaPhoto(media=handle))

            await app.bot.send_media_group(chat_id=tg_id, media=media)
        finally:
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    pass

        await asyncio.sleep(config.TG_SEND_DELAY_SECONDS)


async def send_tiktok_fallback(app: Application, tg_id: int, username: str, post_url: str) -> bool:
    """Fallback: отправляет ссылку на пост текстом; True при успехе."""
    if not post_url:
        return False
    try:
        await app.bot.send_message(
            chat_id=tg_id,
            text=(
                f"TikTok @{username}: не удалось скачать пост, отправляю ссылку:\n"
                f"{post_url}"
            ),
        )
        await asyncio.sleep(config.TG_SEND_DELAY_SECONDS)
        return True
    except Exception as exc:
        logger.error("Ошибка fallback-сообщения TikTok tg_id=%s: %r", tg_id, exc)
        return False


async def _send_fallback_with_reason(
    app: Application, tg_id: int, username: str, post_url: str, reason: str
) -> bool:
    """Fallback-сообщение с человекочитаемой причиной (первые 120 символов)."""
    if not post_url:
        return False
    try:
        await app.bot.send_message(
            chat_id=tg_id,
            text=(
                f"TikTok @{username}: не удалось скачать пост "
                f"({_short_reason(reason)}), отправляю ссылку:\n{post_url}"
            ),
        )
        await asyncio.sleep(config.TG_SEND_DELAY_SECONDS)
        return True
    except Exception as exc:
        logger.error("Ошибка fallback-сообщения TikTok tg_id=%s: %r", tg_id, exc)
        return False


async def _download_with_retry(post_url: str, cookiefile: str | None = None) -> dict:
    """Скачивание с одной повторной попыткой при сетевой ошибке.

    Backoff: base -> min(base * 2^attempt, max) из config. Таймаут НЕ ретраится
    (бюджет поста и так исчерпан).
    """
    base = float(getattr(config, "TIKTOK_RETRY_BACKOFF_BASE_SECONDS", 2.0) or 2.0)
    max_backoff = int(getattr(config, "TIKTOK_RETRY_BACKOFF_MAX_SECONDS", 120) or 120)

    result = await download_tiktok_post(post_url, cookiefile=cookiefile)
    if result.get("ok"):
        return result

    error_text = str(result.get("error", ""))
    if error_text.startswith("timeout "):
        return result

    backoff = min(max(base, 0.0), float(max_backoff))
    if backoff > 0:
        await asyncio.sleep(backoff)

    logger.warning(
        "Повторная попытка скачивания TikTok post после ошибки: %s", _short_reason(error_text)
    )
    return await download_tiktok_post(post_url, cookiefile=cookiefile)


async def send_tiktok_post(
    app: Application, tg_id: int, username: str, post: dict,
    post_timeout_seconds: int | None = None, cookiefile: str | None = None,
) -> tuple[str, str]:
    """Скачивает и отправляет один пост.

    Возвращает (result, reason), result ∈ {'media','partial','fallback','failed'};
    reason — человекочитаемая причина ('download_failed: <err>', 'timeout',
    'send_failed: <err>', 'no_media', '').
    cookiefile — per-user Netscape-файл (или None -> глобальный fallback).
    """
    post_url = post.get("url", "")
    post_id = str(post.get("id", "")).strip()

    if post_timeout_seconds is None:
        post_timeout_seconds = int(getattr(config, "TIKTOK_POST_TIMEOUT_SECONDS", 240) or 240)

    # Скачивание (с ретраем) под общим таймаутом поста. Владение tmp_dir
    # (фикс бага №2): при успехе каталог передаётся в result["tmp_dir"] и
    # удаляется в finally НИЖЕ — после завершения попытки отправки
    # (fail-пути скачивания чистят каталог сами, таймаут оставляет для sweep).
    try:
        result = await asyncio.wait_for(
            _download_with_retry(post_url, cookiefile), timeout=post_timeout_seconds
        )
    except asyncio.TimeoutError:
        return "failed", f"timeout {post_timeout_seconds}s"

    if not result.get("ok"):
        error_text = str(result.get("error", "unknown error"))
        reason = "timeout" if error_text.startswith("timeout ") else f"download_failed: {error_text}"
        logger.warning(
            "Не удалось скачать TikTok post tg_id=%s username=%s post_id=%s reason=%s",
            tg_id, username, post_id, _short_reason(reason),
        )
        if await _send_fallback_with_reason(app, tg_id, username, post_url, reason):
            return "fallback", reason
        return "failed", reason

    webpage_url = result.get("webpage_url") or post_url
    timestamp = safe_int(result.get("timestamp"), 0)
    caption = build_tiktok_caption(username, webpage_url, timestamp)
    kind = result.get("kind")
    files = result.get("files") or []
    tmp_dir = str(result.get("tmp_dir") or "")

    try:
        if kind == "video" and files:
            if len(files) == 1:
                with open(files[0], "rb") as video_handle:
                    await app.bot.send_video(
                        chat_id=tg_id,
                        video=video_handle,
                        caption=caption,
                        supports_streaming=True,
                    )
                await asyncio.sleep(config.TG_SEND_DELAY_SECONDS)
            else:
                await send_tiktok_media_group(app, tg_id, files, caption, "video")
            return "media", ""

        if kind == "photos" and files:
            expected_count = safe_int(result.get("expected_count"), 0)
            is_partial = _is_partial(kind, len(files), expected_count)

            if is_partial:
                partial_caption = caption + "\n⚠️ Часть медиа недоступна (partial)"
                await send_tiktok_media_group(app, tg_id, files, partial_caption, "photos")
                return "partial", f"partial: {len(files)}/{expected_count} файлов"

            await send_tiktok_media_group(app, tg_id, files, caption, "photos")
            return "media", ""

        reason = "no_media"
        if await _send_fallback_with_reason(app, tg_id, username, webpage_url, reason):
            return "fallback", reason
        return "failed", reason

    except Exception as exc:
        reason = f"send_failed: {exc}"
        logger.error(
            "Ошибка отправки TikTok tg_id=%s username=%s post_id=%s: %r",
            tg_id, username, post_id, exc,
        )
        if await _send_fallback_with_reason(app, tg_id, username, webpage_url, reason):
            return "fallback", reason
        return "failed", reason
    finally:
        # Владелец tmp_dir — эта точка: каталог живёт до завершения попытки
        # отправки (все исходы: media/partial/fallback/failed/exception).
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def _process_post(
    app: Application, tg_id: int, username: str, post: dict, cookiefile: str | None = None
) -> None:
    """Claim → доставка → mark + единое structured-событие tiktok_delivery."""
    post_id = str(post.get("id", "")).strip()
    started = time.monotonic()

    cooldown = int(getattr(config, "TIKTOK_RATE_LIMIT_BACKOFF_SECONDS", 300) or 300)
    if not db.claim_tiktok_post(tg_id, post_id, failed_retry_cooldown_seconds=cooldown):
        return

    try:
        delivery_result, reason = await send_tiktok_post(app, tg_id, username, post, cookiefile=cookiefile)
    except Exception:
        # Один неудачный пост не блокирует цикл: помечаем failed и выходим.
        db.mark_tiktok_post_status(tg_id, post_id, "failed", reason="exception")
        logger.exception(
            "tiktok_delivery event=exception tg_id=%s username=%s post_id=%s", tg_id, username, post_id
        )
        return

    status = _DELIVERY_TO_STATUS.get(delivery_result, "failed")
    db.mark_tiktok_post_status(tg_id, post_id, status, reason=reason)

    duration_ms = int((time.monotonic() - started) * 1000)
    attempts = db.get_tiktok_claim_attempts(tg_id, post_id)
    logger.info(
        "tiktok_delivery event=%s tg_id=%s username=%s post_id=%s result=%s duration_ms=%s attempts=%s reason=%s",
        "delivered", tg_id, username, post_id, delivery_result, duration_ms,
        safe_int(attempts, 1) or 1, _short_reason(reason),
    )


def _prepare_user_cookiefile(tg_id: int) -> tuple[str | None, str | None]:
    """Готовит per-user cookiefile: приоритет БД (tiktok_sessions) > глобальный файл.

    (1) db.get_tiktok_session(tg_id) -> cookies_json -> Netscape (tiktok.login) ->
        tmp-файл (удаляет вызывающий в finally).
    (2) config.TIKTOK_COOKIES_FILE через utils.resolve_tiktok_cookiefile().
    Повреждённый cookies_json -> warning и переход к (2), не падаем.

    Returns:
        (cookiefile_path | None, tmp_path | None); tmp_path не None только для (1).
    """
    session = None
    try:
        session = db.get_tiktok_session(tg_id)
    except Exception:
        logger.exception("Не удалось прочитать tiktok_session tg_id=%s", tg_id)

    if session:
        _sessionid, cookies_json = session
        netscape = cookies_json_to_netscape(cookies_json)
        if netscape:
            try:
                tmp_path = save_cookies_to_temp(netscape)
                logger.info("TikTok cookies: per-user сессия из БД tg_id=%s", tg_id)
                return tmp_path, tmp_path
            except Exception:
                logger.exception("Не удалось записать tmp cookies tg_id=%s", tg_id)
        else:
            logger.warning("cookies_json tg_id=%s повреждён, fallback на глобальный файл", tg_id)

    resolved = resolve_tiktok_cookiefile()
    if resolved:
        logger.info("TikTok cookies: глобальный TIKTOK_COOKIES_FILE tg_id=%s", tg_id)
    return resolved, None


async def check_and_send_new_tiktoks(app: Application, tg_id: int, username: str):
    """Цикл мониторинга: initial-sync (1 пост за раз) -> normal (все новые).

    Бюджет цикла: TIKTOK_CYCLE_TIMEOUT_SECONDS от начала прохода; при исчерпании
    неклеймленные посты остаются на следующий проход. Cookies: per-user из БД
    (приоритет) либо глобальный TIKTOK_COOKIES_FILE; tmp-файл удаляется в finally.

    В начале цикла вызывается sweep_stale_tiktok_tmp_dirs: подчистка
    «осиротевших» tmp-каталогов от предыдущих таймаутов скачивания.
    """
    try:
        removed = sweep_stale_tiktok_tmp_dirs()
        if removed:
            logger.info("sweep tmp-dir: подчистлено %d каталогов TikTok", removed)
    except Exception:
        # Sweep не должен ломать цикл мониторинга ни при каких условиях.
        logger.exception("Ошибка sweep tmp-каталогов TikTok")

    deadline = time.monotonic() + int(
        getattr(config, "TIKTOK_CYCLE_TIMEOUT_SECONDS", 900) or 900
    )

    cookiefile, tmp_cookiefile = _prepare_user_cookiefile(tg_id)
    try:
        await _run_tiktok_cycle(app, tg_id, username, deadline, cookiefile)
    finally:
        if tmp_cookiefile:
            try:
                os.remove(tmp_cookiefile)
            except OSError:
                pass


async def _run_tiktok_cycle(app: Application, tg_id: int, username: str,
                            deadline: float, cookiefile: str | None):
    """Тело цикла мониторинга (вызывается из check_and_send_new_tiktoks)."""
    ok, posts, reason = await get_tiktok_posts(username, cookiefile)
    if not ok:
        logger.warning("Ошибка чтения TikTok списка tg_id=%s username=%s: %s", tg_id, username, reason)
        return

    if not posts:
        return

    sent_ids = db.get_tiktok_sent_ids(tg_id)
    to_send = [post for post in posts if post.get("id") not in sent_ids]
    initial_sync_done, last_dispatch_ts = db.get_tiktok_sync_state(tg_id)

    if not to_send:
        if not initial_sync_done:
            db.set_tiktok_sync_state(tg_id, initial_sync_done=True)
            logger.info("Первичная синхронизация TikTok завершена tg_id=%s username=%s", tg_id, username)
        return

    # Initial full sync mode: send exactly 1 post every gap seconds.
    if not initial_sync_done:
        now = int(time.time())
        if last_dispatch_ts and now - last_dispatch_ts < config.TIKTOK_INITIAL_SYNC_GAP_SECONDS:
            return
        if _post_deadline_exceeded(deadline):
            logger.warning("cycle budget исчерпан (initial-sync) tg_id=%s username=%s", tg_id, username)
            return

        post = to_send[0]
        post_id = str(post.get("id", "")).strip()
        if not post_id:
            return

        await _process_post(app, tg_id, username, post, cookiefile)
        db.set_tiktok_sync_state(tg_id, last_dispatch_ts=int(time.time()))

        # Любой финальный статус считается обработанным: повтор не нужен.
        status = db.get_tiktok_claim_status(tg_id, post_id)
        if status in ("sent", "partial", "fallback"):
            if len(to_send) == 1:
                db.set_tiktok_sync_state(tg_id, initial_sync_done=True)
                logger.info("Первичная синхронизация TikTok завершена tg_id=%s username=%s", tg_id, username)
        return

    # Normal mode after first full sync: only new posts.
    logger.info("Найдено новых TikTok постов tg_id=%s username=%s count=%s", tg_id, username, len(to_send))
    for post in to_send:
        if _post_deadline_exceeded(deadline):
            logger.warning("cycle budget исчерпан tg_id=%s username=%s (неклеймленных: %s)",
                           tg_id, username, len(to_send) - to_send.index(post))
            break
        post_id = str(post.get("id", "")).strip()
        if not post_id:
            continue
        try:
            await _process_post(app, tg_id, username, post, cookiefile)
        except Exception:
            # Страховка: исключение _process_post никогда не прерывает цикл.
            logger.exception(
                "tiktok_delivery event=exception tg_id=%s username=%s post_id=%s",
                tg_id, username, post_id,
            )
            try:
                db.mark_tiktok_post_status(tg_id, post_id, "failed", reason="exception")
            except Exception:
                logger.exception("Не удалось пометить failed tg_id=%s post_id=%s", tg_id, post_id)
