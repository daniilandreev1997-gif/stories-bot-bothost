"""Тесты чистых утилит utils.py (подзадача A3)."""
import re

import pytest

from utils import SARATOV_TZ, extract_tiktok_post_id, format_date, normalize_tiktok_username, safe_int


class TestNormalizeTiktokUsername:
    def test_at_prefix(self):
        assert normalize_tiktok_username("@user") == "user"

    def test_full_url_with_query(self):
        assert normalize_tiktok_username("https://www.tiktok.com/@user?x=1") == "user"

    def test_trailing_slash(self):
        assert normalize_tiktok_username("user/") == "user"

    def test_invalid_chars(self):
        assert normalize_tiktok_username("IN VALID!") == ""

    def test_empty(self):
        assert normalize_tiktok_username("") == ""

    def test_uppercase_lowered(self):
        assert normalize_tiktok_username("UPPER") == "upper"

    def test_short_name_invalid(self):
        # TIKTOK_USERNAME_RE требует минимум 2 символа
        assert normalize_tiktok_username("x") == ""

    def test_none_like_empty(self):
        assert normalize_tiktok_username("   ") == ""


class TestExtractTiktokPostId:
    def test_video_url(self):
        assert extract_tiktok_post_id("https://www.tiktok.com/@u/video/7251234567890") == "7251234567890"

    def test_url_without_video(self):
        assert extract_tiktok_post_id("https://www.tiktok.com/@user?x=1") == ""

    def test_empty_url(self):
        assert extract_tiktok_post_id("") == ""

    def test_partial_word_not_matched(self):
        # \d+ после /video/ — нечисловой хвост не матчится
        assert extract_tiktok_post_id("/video/abc") == ""


class TestSafeInt:
    def test_int_value(self):
        assert safe_int("12") == 12

    def test_bad_value_default(self):
        assert safe_int("abc") == 0

    def test_none_default(self):
        assert safe_int(None, 5) == 5

    def test_float_string(self):
        assert safe_int("3.7") == 0


class TestFormatDate:
    def test_timestamp_formats_ddmmyyyy(self):
        result = format_date(1712345678)
        assert result
        assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", result)

    def test_empty_timestamp(self):
        assert format_date("") == ""
        assert format_date(0) == ""
        assert format_date(None) == ""

    def test_invalid_timestamp(self):
        assert format_date("not-a-number") == ""


def test_saratov_tz_not_none():
    assert SARATOV_TZ is not None
