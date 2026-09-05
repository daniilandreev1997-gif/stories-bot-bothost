"""Тесты db/tiktok_claims.py (Этап 3): атомарный claim, статусы доставки.

Фикстура db_isolated (conftest) подменяет conn во всех субмодулях db
(включая db.tiktok_claims) на изолированную tmp-БД и накатывает миграции
v1..v7. Функции вызываются как через db.*, так и напрямую из db.tiktok_claims.
"""
import db
from db.connection import DB_LOCK
import pytest

from db.tiktok_claims import (
    claim_tiktok_post,
    get_tiktok_claim_attempts,
    get_tiktok_claim_status,
    get_tiktok_sent_ids,
    mark_tiktok_post_status,
)

TG = 7001
POST = "7348593012345678901"
OTHER_POST = "7348593012345678902"


# =======================
# CLAIM
# =======================
def test_claim_first_time_true(db_isolated):
    assert claim_tiktok_post(TG, POST) is True


def test_claim_second_time_false_while_claimed(db_isolated):
    claim_tiktok_post(TG, POST)
    # Статус 'claimed' — второй подряд claim не проходит.
    assert claim_tiktok_post(TG, POST) is False
    # attempts не растёт при отклонённом claim (UPSERT без DO UPDATE).
    assert get_tiktok_claim_attempts(TG, POST) == 1


def test_claim_other_post_independent(db_isolated):
    claim_tiktok_post(TG, POST)
    # Другой (tg_id, post_id) — отдельная строка, claim проходит.
    assert claim_tiktok_post(TG, OTHER_POST) is True
    assert claim_tiktok_post(TG + 1, POST) is True


def test_claim_empty_args_false(db_isolated):
    assert claim_tiktok_post(TG, "") is False
    assert claim_tiktok_post(0, POST) is False


# =======================
# MARK / DELIVERED STATUSES
# =======================
def test_mark_sent_blocks_claim_and_enters_sent_ids(db_isolated):
    claim_tiktok_post(TG, POST)
    mark_tiktok_post_status(TG, POST, "sent")
    assert claim_tiktok_post(TG, POST) is False
    assert get_tiktok_sent_ids(TG) == {POST}


def test_mark_partial_blocks_claim_and_enters_sent_ids(db_isolated):
    claim_tiktok_post(TG, POST)
    mark_tiktok_post_status(TG, POST, "partial")
    assert claim_tiktok_post(TG, POST) is False
    assert get_tiktok_sent_ids(TG) == {POST}


def test_mark_fallback_blocks_claim_and_enters_sent_ids(db_isolated):
    claim_tiktok_post(TG, POST)
    mark_tiktok_post_status(TG, POST, "fallback")
    assert claim_tiktok_post(TG, POST) is False
    assert get_tiktok_sent_ids(TG) == {POST}


def test_delivered_statuses_of_same_user_collected(db_isolated):
    claim_tiktok_post(TG, "p1")
    claim_tiktok_post(TG, "p2")
    claim_tiktok_post(TG, "p3")
    mark_tiktok_post_status(TG, "p1", "sent")
    mark_tiktok_post_status(TG, "p2", "partial")
    mark_tiktok_post_status(TG, "p3", "fallback")
    assert get_tiktok_sent_ids(TG) == {"p1", "p2", "p3"}
    # Другой пользователь не видит чужих доставок.
    assert get_tiktok_sent_ids(TG + 1) == set()


def test_mark_invalid_status_raises_value_error(db_isolated):
    claim_tiktok_post(TG, POST)
    with pytest.raises(ValueError):
        mark_tiktok_post_status(TG, POST, "bogus")


def test_mark_failed_allows_reclaim_after_cooldown(db_isolated):
    claim_tiktok_post(TG, POST)  # attempts=1
    mark_tiktok_post_status(TG, POST, "failed", reason="download_failed: x")

    # Cooldown=600, updated_at только что -> claim отклонён.
    assert claim_tiktok_post(TG, POST, failed_retry_cooldown_seconds=600) is False

    # Искусственное старение updated_at на 700 секунд (прямой SQL под DB_LOCK).
    with DB_LOCK:
        db.conn.execute(
            "UPDATE tiktok_post_claims SET updated_at = updated_at - 700 "
            "WHERE tg_id = ? AND post_id = ?",
            (TG, POST),
        )
        db.conn.commit()

    # Cooldown прошёл -> перезабор, attempts увеличился до 2.
    assert claim_tiktok_post(TG, POST, failed_retry_cooldown_seconds=600) is True
    assert get_tiktok_claim_attempts(TG, POST) == 2
    assert get_tiktok_claim_status(TG, POST) == "claimed"


def test_mark_failed_immediately_reclaimable_with_zero_cooldown(db_isolated):
    claim_tiktok_post(TG, POST)
    mark_tiktok_post_status(TG, POST, "failed")
    assert claim_tiktok_post(TG, POST, failed_retry_cooldown_seconds=0) is True


# =======================
# GETTERS
# =======================
def test_claim_status_none_before_claim(db_isolated):
    assert get_tiktok_claim_status(TG, POST) is None


def test_claim_status_lifecycle(db_isolated):
    claim_tiktok_post(TG, POST)
    assert get_tiktok_claim_status(TG, POST) == "claimed"
    mark_tiktok_post_status(TG, POST, "sent")
    assert get_tiktok_claim_status(TG, POST) == "sent"


def test_claim_attempts_none_before_and_one_after_first_claim(db_isolated):
    assert get_tiktok_claim_attempts(TG, POST) is None
    claim_tiktok_post(TG, POST)
    assert get_tiktok_claim_attempts(TG, POST) == 1


# =======================
# UPSERT-INSERT на mark без claim (фактическое поведение кода)
# =======================
def test_mark_without_prior_claim_upserts_row(db_isolated):
    # ФАКТ кода (db/tiktok_claims.py, mark_tiktok_post_status): строка может
    # не существовать — выполняется INSERT со статусом, attempts=1.
    assert get_tiktok_claim_status(TG, POST) is None
    mark_tiktok_post_status(TG, POST, "fallback", reason="no_media")
    assert get_tiktok_claim_status(TG, POST) == "fallback"
    assert get_tiktok_claim_attempts(TG, POST) == 1
    # fallback считается доставленным -> claim не пройдёт, ID в sent_ids.
    assert claim_tiktok_post(TG, POST) is False
    assert get_tiktok_sent_ids(TG) == {POST}
