"""Таблица tiktok_post_claims: атомарный claim и статусы доставки TikTok-постов.

Заменяет дедупликацию по tiktok_sent для мониторинга (подзадача Этапа 3):
- claim_tiktok_post — атомарный UPSERT под DB_LOCK (без гонок check-then-insert);
- mark_tiktok_post_status — фиксирование результата доставки;
- get_tiktok_sent_ids — новая семантика: post_id строк со статусами
  'sent'/'partial'/'fallback' (не отправлять повторно).

Статусы хранятся латиницей: 'claimed' | 'sent' | 'partial' | 'fallback' | 'failed'.
Старая таблица tiktok_sent и функции db/dedup.py не затрагиваются
(обратная совместимость для тестов).
"""
import logging
import time

from .connection import DB_LOCK, conn

logger = logging.getLogger(__name__)

# Валидные статусы доставки (латиница, неизменяемое множество).
CLAIM_STATUSES: frozenset[str] = frozenset({"claimed", "sent", "partial", "fallback", "failed"})

# Статусы, означающие «пост уже доставлен (или сознательно показан ссылкой) —
# повторная отправка не нужна».
DELIVERED_STATUSES: tuple[str, ...] = ("sent", "partial", "fallback")


def claim_tiktok_post(tg_id: int, post_id: str, failed_retry_cooldown_seconds: int = 600) -> bool:
    """Атомарно забирает пост в работу; True — можно отправлять, False — нельзя.

    Логика (один UPSERT под DB_LOCK, без check-then-insert гонок):
    - строки нет                 -> INSERT со статусом 'claimed', attempts=1 -> True;
    - status='failed' и cooldown
      (updated_at <= now - cooldown) прошёл -> перезабор: status='claimed',
      attempts=attempts+1 -> True;
    - status='failed' и cooldown не прошёл   -> False;
    - status='claimed' (другой воркер)       -> False;
    - status in ('sent','partial','fallback') -> False (уже доставлен).

    Факт срабатывания UPSERT определяется по приросту conn.total_changes:
    INSERT или DO UPDATE дают +1, отклонённый WHERE-конфликт — 0. Тем самым
    «True» означает, что именно ЭТОТ вызов захватил/перезахватил строку.
    """
    post_id = str(post_id or "").strip()
    if not tg_id or not post_id:
        return False

    cooldown = max(0, int(failed_retry_cooldown_seconds))
    now = int(time.time())

    with DB_LOCK:
        changes_before = conn.total_changes
        conn.execute(
            """
            INSERT INTO tiktok_post_claims
                (tg_id, post_id, status, claimed_at, updated_at, attempts)
            VALUES (?, ?, 'claimed', ?, ?, 1)
            ON CONFLICT(tg_id, post_id) DO UPDATE SET
                status = 'claimed',
                attempts = tiktok_post_claims.attempts + 1,
                updated_at = excluded.updated_at
            WHERE tiktok_post_claims.status = 'failed'
              AND (tiktok_post_claims.updated_at IS NULL
                   OR tiktok_post_claims.updated_at <= ? - ?)
            """,
            (tg_id, post_id, now, now, now, cooldown),
        )
        took = conn.total_changes > changes_before
        conn.commit()

    if took:
        logger.debug("tiktok_claim tg_id=%s post_id=%s -> claimed", tg_id, post_id)
    return took


def mark_tiktok_post_status(tg_id: int, post_id: str, status: str, reason: str = "") -> None:
    """Обновляет статус/причину/updated_at строки claim; невалидный статус — ValueError.

    Строка может не существовать (например, mark после внешнего сброса) —
    тогда выполняется UPSERT-insert со статусом (claimed_at = now).
    """
    normalized = str(status or "").strip()
    if normalized not in CLAIM_STATUSES:
        raise ValueError(
            f"Недопустимый статус tiktok_post_claims: {status!r} "
            f"(разрешены: {sorted(CLAIM_STATUSES)})"
        )

    post_id = str(post_id or "").strip()
    now = int(time.time())
    reason_text = str(reason or "").strip() or None

    with DB_LOCK:
        conn.execute(
            """
            INSERT INTO tiktok_post_claims
                (tg_id, post_id, status, claimed_at, updated_at, attempts, reason)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(tg_id, post_id) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (tg_id, post_id, normalized, now, now, reason_text),
        )
        conn.commit()

    logger.debug("tiktok_mark tg_id=%s post_id=%s status=%s", tg_id, post_id, normalized)


def get_tiktok_claim_status(tg_id: int, post_id: str) -> str | None:
    """Текущий статус claim (str) или None, если записи нет."""
    post_id = str(post_id or "").strip()
    with DB_LOCK:
        row = conn.execute(
            "SELECT status FROM tiktok_post_claims WHERE tg_id = ? AND post_id = ?",
            (tg_id, post_id),
        ).fetchone()
    return (str(row[0]) if row and row[0] else None)


def get_tiktok_claim_attempts(tg_id: int, post_id: str) -> int | None:
    """Число попыток доставки (attempts) или None, если записи нет."""
    post_id = str(post_id or "").strip()
    with DB_LOCK:
        row = conn.execute(
            "SELECT attempts FROM tiktok_post_claims WHERE tg_id = ? AND post_id = ?",
            (tg_id, post_id),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def get_tiktok_sent_ids(tg_id: int) -> set[str]:
    """Множество post_id, считающихся доставленными: статусы sent/partial/fallback.

    Новая семантика для monitoring.py — заменяет выборку из tiktok_sent.
    """
    placeholders = ", ".join("?" for _ in DELIVERED_STATUSES)
    with DB_LOCK:
        rows = conn.execute(
            f"SELECT post_id FROM tiktok_post_claims "
            f"WHERE tg_id = ? AND status IN ({placeholders})",
            (tg_id, *DELIVERED_STATUSES),
        ).fetchall()
    return {str(row[0]) for row in rows if row[0]}
