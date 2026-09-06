"""Тесты слоя db на изолированной БД (фикстура db_isolated из conftest).

Проверяются: версия схемы v7, settings, users, dedup (legacy tiktok_sent),
vk_tokens (приоритет get_any_active_vk_token), instagram, tiktok_sessions,
tiktok_claims (новая семантика get_tiktok_sent_ids: статусы
sent/partial/fallback из tiktok_post_claims; старая выборка из tiktok_sent —
get_tiktok_sent_ids_legacy).
"""
import config
import db
import pytest


# =======================
# МИГРАЦИИ / СХЕМА
# =======================
def test_fresh_db_user_version_7(db_isolated):
    assert db.get_user_version() == 7


# =======================
# SETTINGS
# =======================
def test_settings_roundtrip(db_isolated):
    db.set_setting("my_key", "my_value")
    assert db.get_setting("my_key") == "my_value"


def test_settings_missing_returns_empty(db_isolated):
    assert db.get_setting("never_set_key") == ""


def test_settings_overwrite(db_isolated):
    db.set_setting("k", "v1")
    db.set_setting("k", "v2")
    assert db.get_setting("k") == "v2"


# =======================
# USERS
# =======================
def test_save_user_vk_id_and_load(db_isolated):
    db.save_user_vk_id(101, "555777")
    users = db.load_vk_users()
    assert (101, "555777", None) in users
    assert db.get_user_vk_id(101) == "555777"


def test_update_last_story_id(db_isolated):
    db.save_user_vk_id(102, "42")
    db.update_last_story_id(102, "story-999")
    assert (102, "42", "story-999") in db.load_vk_users()


def test_save_user_tiktok_username(db_isolated):
    db.save_user_tiktok_username(201, "some_tictoker")
    assert db.get_user_tiktok_username(201) == "some_tictoker"
    assert (201, "some_tictoker") in db.load_tiktok_users()


def test_tiktok_username_missing_is_none(db_isolated):
    assert db.get_user_tiktok_username(999) is None


# =======================
# DEDUP (tiktok_sent legacy) — старая таблица не тронута Этапом 3
# =======================
def test_mark_tiktok_post_sent_idempotent(db_isolated):
    db.mark_tiktok_post_sent(301, "post-1")
    db.mark_tiktok_post_sent(301, "post-1")  # повтор — INSERT OR IGNORE
    assert db.get_tiktok_sent_ids_legacy(301) == {"post-1"}
    # Новая семантика get_tiktok_sent_ids (claims) НЕ видит tiktok_sent.
    assert db.get_tiktok_sent_ids(301) == set()


def test_clear_tiktok_sent_for_user(db_isolated):
    db.mark_tiktok_post_sent(302, "a")
    db.mark_tiktok_post_sent(302, "b")
    db.mark_tiktok_post_sent(303, "c")  # другой пользователь не затрагивается
    db.clear_tiktok_sent_for_user(302)
    assert db.get_tiktok_sent_ids_legacy(302) == set()
    assert db.get_tiktok_sent_ids_legacy(303) == {"c"}


# =======================
# VK TOKENS
# =======================
def test_vk_user_token_roundtrip(db_isolated):
    db.save_vk_user_token(401, "vk-token-plain-XYZ")
    assert db.get_vk_user_token(401) == "vk-token-plain-XYZ"


def test_vk_user_credentials_roundtrip(db_isolated):
    db.save_vk_user_password(402, "user@login.com", "p@ssw0rd")
    assert db.get_vk_user_credentials(402) == ("user@login.com", "p@ssw0rd")


def test_get_any_active_vk_token_priority_override(db_isolated, monkeypatch):
    db.save_vk_user_token(403, "user-token")
    monkeypatch.setattr(config, "VK_TOKEN", "config-token")
    db.set_setting("vk_token_override", "override-token")
    assert db.get_any_active_vk_token() == "override-token"


def test_get_any_active_vk_token_priority_config(db_isolated, monkeypatch):
    db.save_vk_user_token(404, "user-token")
    monkeypatch.setattr(config, "VK_TOKEN", "config-token")
    assert db.get_any_active_vk_token() == "config-token"


def test_get_any_active_vk_token_priority_user_token(db_isolated, monkeypatch):
    monkeypatch.setattr(config, "VK_TOKEN", "")
    db.save_vk_user_token(405, "only-user-token")
    assert db.get_any_active_vk_token() == "only-user-token"


def test_get_any_active_vk_token_service_tier(db_isolated, monkeypatch):
    """Сервисная ступень: VK_TOKEN="" и user-токенов нет, VK_SERVICE_KEY задан."""
    monkeypatch.setattr(config, "VK_TOKEN", "")
    monkeypatch.setattr(config, "VK_SERVICE_KEY", "service-key-test")
    assert db.get_any_active_vk_token() == "service-key-test"


def test_get_any_active_vk_token_service_last_priority(db_isolated, monkeypatch):
    """Сервисная ступень последняя: user-токен + сервисный -> побеждает user."""
    db.save_vk_user_token(409, "user-token-1")
    monkeypatch.setattr(config, "VK_TOKEN", "")
    monkeypatch.setattr(config, "VK_SERVICE_KEY", "service-key-test")
    assert db.get_any_active_vk_token() == "user-token-1"


@pytest.mark.parametrize(
    "which,expected_tier",
    [
        ("override", "override"),
        ("config", "config"),
        ("user", "user"),
        ("service", "service"),
        ("none", ""),
    ],
)
def test_get_any_active_vk_token_with_tier_matrix(db_isolated, monkeypatch, which, expected_tier):
    """Матрица ступеней get_any_active_vk_token_with_tier: override→config→user→service→none."""
    monkeypatch.setattr(config, "VK_TOKEN", "config-token" if which == "config" else "")
    monkeypatch.setattr(config, "VK_SERVICE_KEY", "service-key-test" if which == "service" else "")

    if which == "override":
        db.set_setting("vk_token_override", "override-token")
    if which == "user":
        db.save_vk_user_token(410, "user-token-2")

    token, tier = db.get_any_active_vk_token_with_tier()

    if expected_tier:
        assert tier == expected_tier
        assert token, f"tier={tier}: токен обязан возвращаться"
    else:
        assert tier == ""
        assert token is None


def test_delete_vk_user_token(db_isolated):
    db.save_vk_user_token(406, "to-be-deleted")
    db.delete_vk_user_token(406)
    assert db.get_vk_user_token(406) is None


def test_password_kind_returns_token_none(db_isolated):
    db.save_vk_user_password(407, "login", "pass")
    assert db.get_vk_user_token(407) is None


def test_token_kind_returns_credentials_none(db_isolated):
    db.save_vk_user_token(408, "tok")
    assert db.get_vk_user_credentials(408) is None


# =======================
# INSTAGRAM
# =======================
def test_instagram_credentials_roundtrip(db_isolated):
    db.save_instagram_credentials(501, "ig_user", "ig_password_секрет")
    settings = db.get_instagram_settings(501)
    assert settings is not None
    assert settings["username"] == "ig_user"
    assert settings["password"] == "ig_password_секрет"  # расшифрован
    assert settings["session_valid"] is False


def test_instagram_verification_session_pop(db_isolated):
    challenge = {"step": "1", "endpoint": "/challenge"}
    db.save_instagram_verification_session(502, "ig_u", "ig_p", challenge)
    result = db.pop_instagram_verification_session(502)
    assert result is not None
    username, password, challenge_json = result
    assert username == "ig_u"
    assert password == "ig_p"
    assert '"step"' in challenge_json
    # pop забирает и очищает: повторный вызов -> None
    assert db.pop_instagram_verification_session(502) is None


def test_set_instagram_session_invalid(db_isolated):
    db.save_instagram_session(503, {"cookie": "v"})
    assert db.get_instagram_settings(503)["session_valid"] is True
    db.set_instagram_session_invalid(503)
    assert db.get_instagram_settings(503)["session_valid"] is False


# =======================
# TIKTOK SESSIONS / STATS
# =======================
def test_tiktok_session_roundtrip(db_isolated):
    cookies = '{"sessionid_ss": "abc"}'
    db.save_tiktok_session(601, "sessionid-123", cookies)
    session = db.get_tiktok_session(601)
    assert session == ("sessionid-123", cookies)


def test_tiktok_session_missing(db_isolated):
    assert db.get_tiktok_session(602) is None


def test_tiktok_stats_upsert_get(db_isolated):
    db.upsert_tiktok_stats(603, "tiktoker", followers=100, hearts=2000, video_count=30,
                           fetched_at=1712345678)
    stats = db.get_tiktok_stats(603, "tiktoker")
    assert stats == {"tg_id": 603, "username": "tiktoker", "followers": 100, "hearts": 2000,
                     "video_count": 30, "fetched_at": 1712345678}
    # upsert заменяет значения
    db.upsert_tiktok_stats(603, "tiktoker", followers=101, hearts=2100, video_count=31,
                           fetched_at=1712345999)
    assert db.get_tiktok_stats(603, "tiktoker")["followers"] == 101


def test_tiktok_stats_missing(db_isolated):
    assert db.get_tiktok_stats(604, "nobody") is None
