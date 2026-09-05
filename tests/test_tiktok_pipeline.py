"""Тесты tiktok-пайплайна без сети (Этап 3): resolve cookiefile, extract с
моком yt_dlp, обрезка reason, сбор скачанных медиа, эвристика partial.

Сеть не используется: yt_dlp подменяется FakeYDL (monkeypatch модульного
атрибута tiktok.extract.yt_dlp), resolve_tiktok_cookiefile работает на
tmp-файлах через monkeypatch на config.
"""
import config
import pytest

import tiktok.extract as tiktok_extract
import tiktok.monitoring as tiktok_monitoring
from tiktok.download import _collect_downloaded_media
from tiktok.extract import _extract_tiktok_posts_sync
from tiktok.monitoring import _is_partial, _post_deadline_exceeded, _short_reason
from utils import resolve_tiktok_cookiefile


# =======================
# utils.resolve_tiktok_cookiefile (monkeypatch на config)
# =======================
def test_resolve_cookiefile_empty_config_none(monkeypatch):
    monkeypatch.setattr(config, "TIKTOK_COOKIES_FILE", "")
    assert resolve_tiktok_cookiefile() is None


def test_resolve_cookiefile_valid_netscape_file(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".tiktok.com\tTRUE\t/\tTRUE\t1740000000\tsessionid\tabc123\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "TIKTOK_COOKIES_FILE", str(cookie_file))
    resolved = resolve_tiktok_cookiefile()
    assert resolved is not None
    assert str(resolved) == str(cookie_file.resolve())


def test_resolve_cookiefile_garbage_file_none(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(config, "TIKTOK_COOKIES_FILE", str(cookie_file))
    assert resolve_tiktok_cookiefile() is None


def test_resolve_cookiefile_missing_file_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TIKTOK_COOKIES_FILE", str(tmp_path / "nope.txt"))
    assert resolve_tiktok_cookiefile() is None


def test_resolve_cookiefile_header_variant_http_cookie_file(tmp_path, monkeypatch):
    """ФАКТ кода: маркер '# HTTP Cookie File' тоже валиден (маркер-набор)."""
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# HTTP Cookie File\n.c\tTRUE\t/\tTRUE\t0\tn\tv\n", encoding="utf-8")
    monkeypatch.setattr(config, "TIKTOK_COOKIES_FILE", str(cookie_file))
    assert resolve_tiktok_cookiefile() is not None


# =======================
# tiktok.extract._extract_tiktok_posts_sync (мок модуля yt_dlp)
# =======================
# ФАКТ кода: tiktok.extract держит МОДУЛЬ yt_dlp и зовёт
# yt_dlp.YoutubeDL(ydl_opts) — подменяется fake-модуль с атрибутом YoutubeDL.
class FakeYDL:
    """Мок yt_dlp.YoutubeDL: extract_info отдаёт фиксированные entries."""

    last_opts: dict = {}

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        assert download is False
        assert "@u" in url  # профиль строится как https://www.tiktok.com/@u
        return {
            "entries": [
                {
                    "id": "7348593012345678901",
                    "url": "https://www.tiktok.com/@u/video/7348593012345678901",
                    "timestamp": 1700000000,
                },
                {
                    # id не числовой, URL /photo/ без /video/<digits> -> SKIP
                    "id": "photo-xyz-не-число",
                    "url": "https://www.tiktok.com/@u/photo/999",
                },
            ]
        }


class FakeYTDlpModule:
    """Fake-модуль yt_dlp: подставляется на место tiktok.extract.yt_dlp."""

    def __init__(self, ydl_cls=FakeYDL):
        self.YoutubeDL = ydl_cls


def test_extract_sync_numeric_id_kept_photo_skipped(monkeypatch):
    monkeypatch.setattr(tiktok_extract, "yt_dlp", FakeYTDlpModule())
    posts = _extract_tiktok_posts_sync("u")

    # ФАКТ кода: пост с числовым id включён, /photo/-пост без числового id
    # скипнут (URL как ID запрещён, /photo/ не парсится как /video/<digits>).
    assert len(posts) == 1
    assert posts[0]["id"] == "7348593012345678901"
    assert posts[0]["url"] == "https://www.tiktok.com/@u/video/7348593012345678901"
    assert posts[0]["timestamp"] == 1700000000


def test_extract_sync_id_from_video_url_fallback(monkeypatch):
    """Entry без id, но URL с /video/<digits> -> id из URL."""

    class FakeYDLUrlId(FakeYDL):
        def extract_info(self, url, download=False):
            return {
                "entries": [
                    {
                        # id отсутствует/нечисловой, но URL содержит /video/123...
                        "url": "https://www.tiktok.com/@u/video/123456789",
                    }
                ]
            }

    monkeypatch.setattr(tiktok_extract, "yt_dlp", FakeYTDlpModule(FakeYDLUrlId))
    posts = _extract_tiktok_posts_sync("u")
    assert len(posts) == 1
    assert posts[0]["id"] == "123456789"
    assert posts[0]["url"] == "https://www.tiktok.com/@u/video/123456789"


def test_extract_sync_uses_cookiefile_when_provided(tmp_path, monkeypatch):
    """Переданный cookiefile приоритетнее глобального (факт _build_ydl_opts)."""
    cookie_file = tmp_path / "user_cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n.c\tTRUE\t/\tTRUE\t0\tn\tv\n", encoding="utf-8"
    )

    class FakeYDLCookieProbe(FakeYDL):
        def extract_info(self, url, download=False):
            assert self.opts.get("cookiefile") == str(cookie_file)
            return {"entries": []}

    monkeypatch.setattr(tiktok_extract, "yt_dlp", FakeYTDlpModule(FakeYDLCookieProbe))
    assert _extract_tiktok_posts_sync("u", cookiefile=str(cookie_file)) == []


def test_extract_sync_empty_entries(monkeypatch):
    class FakeYDLEmpty(FakeYDL):
        def extract_info(self, url, download=False):
            return {"entries": []}

    monkeypatch.setattr(tiktok_extract, "yt_dlp", FakeYTDlpModule(FakeYDLEmpty))
    assert _extract_tiktok_posts_sync("u") == []


# =======================
# tiktok.monitoring._short_reason
# =======================
def test_short_reason_truncates_to_120():
    long_reason = "x" * 500
    assert len(_short_reason(long_reason)) == 120
    assert _short_reason(long_reason) == "x" * 120


def test_short_reason_empty():
    assert _short_reason("") == ""
    assert _short_reason(None) == ""


def test_short_reason_collapses_whitespace():
    assert _short_reason("download   failed:\n  boom\t!") == "download failed: boom !"


# =======================
# tiktok.download._collect_downloaded_media
# =======================
def test_collect_downloaded_media_filters(tmp_path):
    # a.mp4 (100 байт) -> video; b.jpg (50) -> image;
    # c.part (100) и d.tmp (10) -> SKIP_EXTENSIONS; e.mp4 (0 байт) -> пустой.
    (tmp_path / "a.mp4").write_bytes(b"v" * 100)
    (tmp_path / "b.jpg").write_bytes(b"i" * 50)
    (tmp_path / "c.part").write_bytes(b"p" * 100)
    (tmp_path / "d.tmp").write_bytes(b"t" * 10)
    (tmp_path / "e.mp4").write_bytes(b"")

    video_files, image_files = _collect_downloaded_media(str(tmp_path))
    assert [str(tmp_path / "a.mp4")] == video_files
    assert [str(tmp_path / "b.jpg")] == image_files
    all_paths = video_files + image_files
    assert str(tmp_path / "c.part") not in all_paths
    assert str(tmp_path / "d.tmp") not in all_paths
    assert str(tmp_path / "e.mp4") not in all_paths


def test_collect_downloaded_media_missing_dir(tmp_path):
    video_files, image_files = _collect_downloaded_media(str(tmp_path / "missing"))
    assert video_files == []
    assert image_files == []


# =======================
# tiktok.monitoring._is_partial (чистая эвристика partial)
# =======================
@pytest.mark.parametrize(
    "kind,files_count,expected_count,want",
    [
        ("photos", 3, 5, True),
        ("photos", 5, 5, False),
        ("video", 1, 5, False),
        ("photos", 3, 0, False),
        ("photos", 0, 5, True),   # ни одного файла из 5 — тоже partial
        ("photos", 6, 5, False),  # больше ожидаемого — не partial
    ],
)
def test_is_partial_matrix(kind, files_count, expected_count, want):
    assert _is_partial(kind, files_count, expected_count) is want


def test_post_deadline_exceeded():
    import time

    assert _post_deadline_exceeded(None) is False
    assert _post_deadline_exceeded(time.monotonic() - 1) is True
    assert _post_deadline_exceeded(time.monotonic() + 60) is False
