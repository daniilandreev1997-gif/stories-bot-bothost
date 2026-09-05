"""Тесты tiktok/login.py и парсинга ввода tg/flows.py (Этап 3, Часть 2).

Чистые конвертеры cookies в Netscape-формат, temp-файл, статус
"unavailable" при отсутствии playwright (monkeypatch на модульный флаг —
тест стабилен независимо от наличия playwright в окружении), парсер
'email:password' и маска email из tg/flows.py.
"""
import asyncio
import os

import pytest

import tiktok.login as tiktok_login
from tiktok.login import (
    cookies_json_to_netscape,
    cookies_to_netscape,
    login_tiktok,
    save_cookies_to_temp,
)
from tg.flows import _mask_email, _parse_login_input


# =======================
# cookies_to_netscape
# =======================
SYNTHETIC_COOKIES = [
    {
        "name": "sessionid",
        "value": "abc123",
        "domain": ".tiktok.com",
        "path": "/",
        "expires": 1740000000,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    },
    {
        "name": "tt-target",
        "value": "v1",
        "domain": ".tiktok.com",
        "path": "/",
        # expires отсутствует -> сессионная -> 0
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    },
]


def test_cookies_to_netscape_format():
    text = cookies_to_netscape(SYNTHETIC_COOKIES)

    assert text.startswith("# HTTP Cookie File")
    # LF, не CRLF.
    assert "\r" not in text
    assert text.endswith("\n")

    lines = text.split("\n")
    data_lines = [line for line in lines if line and not line.startswith("#")]
    assert len(data_lines) == 2

    first = data_lines[0].split("\t")
    assert len(first) == 7
    domain, flag, path, secure, expiry, name, value = first
    assert domain == ".tiktok.com"
    assert flag == "TRUE"          # у cookies_to_netscape flag всегда TRUE
    assert path == "/"
    assert secure == "TRUE"
    assert expiry == "1740000000"
    assert name == "sessionid"
    assert value == "abc123"

    second = data_lines[1].split("\t")
    assert len(second) == 7
    assert second[4] == "0"        # сессионная cookie -> expires=0
    assert second[3] == "FALSE"    # secure=False
    assert second[5] == "tt-target"
    assert second[6] == "v1"


def test_cookies_to_netscape_empty_list_only_header():
    text = cookies_to_netscape([])
    assert text == "# HTTP Cookie File\n"
    # Пустые/None — тоже только заголовок.
    assert cookies_to_netscape(None) == "# HTTP Cookie File\n"


def test_cookies_to_netscape_skips_cookie_without_name():
    text = cookies_to_netscape([{"value": "orphan", "domain": ".tiktok.com"}])
    assert text == "# HTTP Cookie File\n"


# =======================
# cookies_json_to_netscape
# =======================
def test_cookies_json_to_netscape_valid_json():
    import json

    text = cookies_json_to_netscape(json.dumps(SYNTHETIC_COOKIES))
    assert text is not None
    assert text.startswith("# HTTP Cookie File")
    assert "sessionid\tabc123" in text


def test_cookies_json_to_netscape_broken_json_none():
    assert cookies_json_to_netscape('{"broken') is None


def test_cookies_json_to_netscape_empty_and_not_list():
    assert cookies_json_to_netscape("") is None
    assert cookies_json_to_netscape(None) is None
    assert cookies_json_to_netscape('{"a": 1}') is None  # JSON не список


# =======================
# save_cookies_to_temp
# =======================
def test_save_cookies_to_temp_writes_file(tmp_path, monkeypatch):
    # Направляем tempfile в tmp_path, чтобы гарантированно не сорить в системе.
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))

    netscape = cookies_to_netscape(SYNTHETIC_COOKIES)
    path = save_cookies_to_temp(netscape)
    try:
        assert os.path.isfile(path)
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert content == netscape
        assert content.startswith("# HTTP Cookie File")
    finally:
        if os.path.exists(path):
            os.remove(path)
    assert not os.path.exists(path)


# =======================
# login_tiktok: playwright недоступен (стабильно, без сети)
# =======================
def test_login_tiktok_unavailable_without_playwright(monkeypatch):
    """ФАКТ кода: PLAYWRIGHT_AVAILABLE=False -> статус 'unavailable' с reason."""
    monkeypatch.setattr(tiktok_login, "PLAYWRIGHT_AVAILABLE", False)
    result = asyncio.run(login_tiktok("a@b.c", "pw"))
    assert result["status"] == "unavailable"
    # Точный ключ reason — по факту кода: "playwright not installed".
    assert result.get("reason") == "playwright not installed"


def test_login_tiktok_is_async_callable(monkeypatch):
    """Санити: login_tiktok — coroutine-функция (async def)."""
    monkeypatch.setattr(tiktok_login, "PLAYWRIGHT_AVAILABLE", False)
    coro = login_tiktok("a@b.c", "pw")
    assert asyncio.iscoroutine(coro)
    assert asyncio.run(coro)["status"] == "unavailable"


# =======================
# tg.flows._parse_login_input / _mask_email
# =======================
def test_parse_login_input_valid():
    assert _parse_login_input("user@example.com:secret") == ("user@example.com", "secret")
    # Пароль может содержать ':' — split по первому.
    assert _parse_login_input("user@example.com:pa:ss:wd") == ("user@example.com", "pa:ss:wd")
    # Пробелы вокруг полей срезаются.
    assert _parse_login_input("  user@example.com : secret  ") == ("user@example.com", "secret")


def test_parse_login_input_no_colon_invalid():
    assert _parse_login_input("user@example.com") is None
    assert _parse_login_input("") is None


def test_parse_login_input_empty_password_invalid():
    assert _parse_login_input("user@example.com:") is None
    assert _parse_login_input("user@example.com:   ") is None


def test_parse_login_input_email_without_at_invalid():
    assert _parse_login_input("username:secret") is None


def test_mask_email():
    assert _mask_email("user@example.com") == "us***"
    assert _mask_email("u@x") == "u@***"  # первые 2 символа + '***'
    assert _mask_email("u") == "***"      # len < 2 -> только маска
    assert _mask_email("") == "***"
    assert _mask_email(None) == "***"
    # Секрет не «утекает»: маска не содержит домен.
    assert _mask_email("sensitive.name@corp.tld") == "se***"
