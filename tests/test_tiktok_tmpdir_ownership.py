"""Тесты контракта владения tmp_dir (баг №2) и sweep-утилиты.

Контракт:
- владелец каталога — точка обработки поста (send_tiktok_post в monitoring):
  каталог живёт до завершения попытки отправки и удаляется в finally;
- download_tiktok_post: fail-пути (исключение/нет медиа) чистят каталог сами,
  успех передаёт владение через result["tmp_dir"], таймаут НЕ удаляет каталог
  (executor-поток ещё пишет) — «осиротевший» каталог убирает sweep;
- sweep_stale_tiktok_tmp_dirs удаляет только старые tiktok_post_* каталоги
  (по st_mtime старше порога), чужие каталоги не трогает;
- check_and_send_new_tiktoks вызывает sweep в начале цикла.

Сеть не используется: yt_dlp подменяется fake-модулем (по образцу
test_tiktok_pipeline), Telegram-объекты — фейками.
"""
import asyncio
import os
import threading
import time

import config

import tiktok.download as tiktok_download
import tiktok.monitoring as tiktok_monitoring


# =======================
# Фейки: mkdtemp-захват, yt_dlp, Telegram
# =======================
def _make_mkdtemp_capture(root: str, created: list):
    """Fake tempfile.mkdtemp: каталоги создаются под root и запоминаются."""

    def fake_mkdtemp(*args, **kwargs):
        path = os.path.join(root, f"tiktok_post_{len(created)}")
        os.makedirs(path, exist_ok=True)
        created.append(path)
        return path

    return fake_mkdtemp


class _WritingYDL:
    """Fake yt_dlp.YoutubeDL: «скачивает» файл в каталог из opts['outtmpl']."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        out_dir = os.path.dirname(self.opts["outtmpl"])
        with open(os.path.join(out_dir, "123_video.mp4"), "wb") as fh:
            fh.write(b"v" * 32)
        return {"id": "123", "webpage_url": url, "timestamp": 1700000000}


class _RaisingYDL(_WritingYDL):
    """Fake yt_dlp: extract_info падает исключением (сетевая ошибка)."""

    def extract_info(self, url, download=True):
        raise RuntimeError("network boom")


class _EmptyYDL(_WritingYDL):
    """Fake yt_dlp: ничего не скачивает (медиа не найдены)."""

    def extract_info(self, url, download=True):
        return {"id": "125", "webpage_url": url}


class _FakeYTDlpModule:
    """Fake-модуль yt_dlp: подставляется на место tiktok.download.yt_dlp."""

    def __init__(self, ydl_cls=_WritingYDL):
        self.YoutubeDL = ydl_cls


def _assert_alive(path: str) -> None:
    """Провалка теста, если файла/каталога нет в момент вызова отправки."""
    if not os.path.exists(path):
        raise AssertionError(f"файл исчез до отправки: {path}")


class _FakeBot:
    """Fake Telegram bot: запись вызовов, probe-проверки, управляемые ошибки."""

    def __init__(self):
        self.calls = []
        self.fail_methods = {}
        self.probes = {}

    def _handle(self, name, kwargs):
        self.calls.append((name, kwargs))
        probe = self.probes.get(name)
        if probe:
            probe()
        exc = self.fail_methods.get(name)
        if exc is not None:
            raise exc

    async def send_video(self, **kwargs):
        self._handle("send_video", kwargs)

    async def send_media_group(self, **kwargs):
        self._handle("send_media_group", kwargs)

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        return True


class _FakeApp:
    def __init__(self, bot):
        self.bot = bot


def _fake_download_ok(tmp_dir, files, kind, expected_count=0):
    """Fake _download_with_retry: успех, владение tmp_dir передаётся наверх."""

    async def _fake(post_url, cookiefile=None):
        return {
            "ok": True,
            "kind": kind,
            "files": list(files),
            "tmp_dir": str(tmp_dir),
            "post_id": "1",
            "timestamp": 0,
            "webpage_url": post_url,
            "expected_count": expected_count,
        }

    return _fake


def _run(coro):
    return asyncio.run(coro)


# =======================
# 1-4: download_tiktok_post — владение tmp_dir
# =======================
def test_download_success_preserves_tmp_dir(tmp_path, monkeypatch):
    """ok=True -> tmp_dir и скачанные файлы существуют (владение передано)."""
    created = []
    monkeypatch.setattr(
        tiktok_download.tempfile, "mkdtemp", _make_mkdtemp_capture(str(tmp_path), created)
    )
    monkeypatch.setattr(tiktok_download, "yt_dlp", _FakeYTDlpModule(_WritingYDL))

    result = _run(tiktok_download.download_tiktok_post("https://www.tiktok.com/@u/video/123"))

    assert result.get("ok") is True
    tmp_dir = result.get("tmp_dir")
    assert tmp_dir and os.path.isdir(tmp_dir)
    files = result.get("files") or []
    assert files, "файлы не собраны"
    assert all(os.path.isfile(p) for p in files)
    assert created and os.path.isdir(created[0])


def test_download_failure_removes_tmp_dir(tmp_path, monkeypatch):
    """FakeYDL кидает исключение -> ok=False, каталог удалён самим download."""
    created = []
    monkeypatch.setattr(
        tiktok_download.tempfile, "mkdtemp", _make_mkdtemp_capture(str(tmp_path), created)
    )
    monkeypatch.setattr(tiktok_download, "yt_dlp", _FakeYTDlpModule(_RaisingYDL))

    result = _run(tiktok_download.download_tiktok_post("https://www.tiktok.com/@u/video/124"))

    assert result.get("ok") is False
    assert len(created) == 1
    assert not os.path.exists(created[0]), "fail-путь обязан удалить каталог"


def test_download_no_media_removes_tmp_dir(tmp_path, monkeypatch):
    """Медиа не найдены -> ok=False, каталог удалён."""
    created = []
    monkeypatch.setattr(
        tiktok_download.tempfile, "mkdtemp", _make_mkdtemp_capture(str(tmp_path), created)
    )
    monkeypatch.setattr(tiktok_download, "yt_dlp", _FakeYTDlpModule(_EmptyYDL))

    result = _run(tiktok_download.download_tiktok_post("https://www.tiktok.com/@u/video/125"))

    assert result.get("ok") is False
    assert len(created) == 1
    assert not os.path.exists(created[0])


_SLOW_EVENT = threading.Event()


class _SlowYDL(_WritingYDL):
    """Fake yt_dlp: «долго пишет» — имитация executor-потока при таймауте."""

    def extract_info(self, url, download=True):
        _SLOW_EVENT.wait(5.0)
        out_dir = os.path.dirname(self.opts["outtmpl"])
        with open(os.path.join(out_dir, "126_video.mp4"), "wb") as fh:
            fh.write(b"v" * 32)
        return {"id": "126", "webpage_url": url, "timestamp": 1700000000}


def test_download_timeout_leaves_tmp_dir_for_sweep(tmp_path, monkeypatch):
    """Таймаут -> ok=False, каталог НЕ удалён (поток ещё пишет) — для sweep."""
    created = []
    monkeypatch.setattr(
        tiktok_download.tempfile, "mkdtemp", _make_mkdtemp_capture(str(tmp_path), created)
    )
    monkeypatch.setattr(tiktok_download, "yt_dlp", _FakeYTDlpModule(_SlowYDL))
    _SLOW_EVENT.clear()
    try:
        result = _run(
            tiktok_download.download_tiktok_post(
                "https://www.tiktok.com/@u/video/126", timeout_seconds=1
            )
        )
        assert result.get("ok") is False
        assert "timeout" in str(result.get("error", ""))
        assert len(created) == 1
        assert os.path.isdir(created[0]), "осиротевший каталог должен остаться для sweep"
    finally:
        _SLOW_EVENT.set()  # отпускаем executor-поток


# =======================
# 5-7: send_tiktok_post — очистка tmp_dir в finally
# =======================
def test_send_tiktok_post_cleans_tmp_dir_after_success(tmp_path, monkeypatch):
    """Успешная отправка video -> после возврата каталога нет; файл жив при отправке."""
    tmp_dir = tmp_path / "tiktok_post_x"
    tmp_dir.mkdir()
    video = tmp_dir / "v.mp4"
    video.write_bytes(b"v" * 32)

    bot = _FakeBot()
    bot.probes["send_video"] = lambda: _assert_alive(str(video))
    monkeypatch.setattr(
        tiktok_monitoring, "_download_with_retry", _fake_download_ok(tmp_dir, [str(video)], "video")
    )
    monkeypatch.setattr(config, "TG_SEND_DELAY_SECONDS", 0)

    result, reason = _run(
        tiktok_monitoring.send_tiktok_post(
            _FakeApp(bot), 1, "u", {"url": "https://www.tiktok.com/@u/video/1", "id": "1"}
        )
    )

    assert result == "media", f"ожидалась отправка медиа, получено: {result} / {reason}"
    assert not tmp_dir.exists(), "каталог обязан быть удалён после отправки"


def test_send_tiktok_post_cleans_tmp_dir_after_send_error(tmp_path, monkeypatch):
    """send_video кидает FileNotFoundError -> каталог всё равно удалён (finally)."""
    tmp_dir = tmp_path / "tiktok_post_y"
    tmp_dir.mkdir()
    video = tmp_dir / "v.mp4"
    video.write_bytes(b"v" * 32)

    bot = _FakeBot()
    bot.probes["send_video"] = lambda: _assert_alive(str(video))
    bot.fail_methods["send_video"] = FileNotFoundError("file vanished")
    monkeypatch.setattr(
        tiktok_monitoring, "_download_with_retry", _fake_download_ok(tmp_dir, [str(video)], "video")
    )
    monkeypatch.setattr(config, "TG_SEND_DELAY_SECONDS", 0)

    result, reason = _run(
        tiktok_monitoring.send_tiktok_post(
            _FakeApp(bot), 1, "u", {"url": "https://www.tiktok.com/@u/video/1", "id": "1"}
        )
    )

    assert result in ("fallback", "failed")
    assert "send_failed" in reason
    assert not tmp_dir.exists(), "каталог обязан быть удалён даже при ошибке отправки"


def test_send_tiktok_media_group_multi_file_cleanup(tmp_path, monkeypatch):
    """photos, 2+ файлов: все чанки получают живые файлы; после — каталога нет."""
    tmp_dir = tmp_path / "tiktok_post_z"
    tmp_dir.mkdir()
    photo1 = tmp_dir / "a.jpg"
    photo2 = tmp_dir / "b.jpg"
    photo1.write_bytes(b"p" * 16)
    photo2.write_bytes(b"q" * 16)
    files = [str(photo1), str(photo2)]

    bot = _FakeBot()

    def _probe_media_group():
        _assert_alive(str(photo1))
        _assert_alive(str(photo2))

    bot.probes["send_media_group"] = _probe_media_group
    monkeypatch.setattr(
        tiktok_monitoring,
        "_download_with_retry",
        _fake_download_ok(tmp_dir, files, "photos", expected_count=2),
    )
    monkeypatch.setattr(config, "TG_SEND_DELAY_SECONDS", 0)

    result, reason = _run(
        tiktok_monitoring.send_tiktok_post(
            _FakeApp(bot), 1, "u", {"url": "https://www.tiktok.com/@u/photo/1", "id": "1"}
        )
    )

    assert result == "media", f"ожидалась отправка медиагруппы, получено: {result} / {reason}"
    assert any(name == "send_media_group" for name, _ in bot.calls)
    assert not tmp_dir.exists(), "каталог удаляется только после ВСЕХ чанков"


# =======================
# 8-9: sweep-утилита и вызов в начале цикла
# =======================
def test_sweep_stale_tiktok_tmp_dirs_removes_old_only(tmp_path):
    """Старый tiktok_post_* удаляется, свежий и чужой каталоги остаются."""
    from tiktok.download import sweep_stale_tiktok_tmp_dirs

    old = tmp_path / "tiktok_post_old"
    old.mkdir()
    fresh = tmp_path / "tiktok_post_fresh"
    fresh.mkdir()
    other = tmp_path / "unrelated_dir"
    other.mkdir()

    stale_ts = time.time() - 7200
    os.utime(old, (stale_ts, stale_ts))

    removed = sweep_stale_tiktok_tmp_dirs(3600, base_dir=str(tmp_path))

    assert not old.exists(), "старый каталог должен быть удалён"
    assert fresh.exists(), "свежий каталог остаётся"
    assert other.exists(), "чужие каталоги не трогаются"
    assert removed == 1


def test_run_cycle_invokes_sweep_at_start(monkeypatch):
    """check_and_send_new_tiktoks вызывает sweep ровно 1 раз ДО чтения постов."""
    sweep_calls = []

    def fake_sweep(max_age_seconds=None):
        sweep_calls.append(max_age_seconds)
        return 0

    async def fake_get_posts(username, cookiefile=None):
        assert sweep_calls, "sweep должен вызываться ДО чтения списка постов"
        return True, [], ""

    monkeypatch.setattr(tiktok_monitoring, "sweep_stale_tiktok_tmp_dirs", fake_sweep)
    monkeypatch.setattr(tiktok_monitoring, "get_tiktok_posts", fake_get_posts)
    monkeypatch.setattr(tiktok_monitoring, "_prepare_user_cookiefile", lambda tg_id: (None, None))

    _run(tiktok_monitoring.check_and_send_new_tiktoks(_FakeApp(_FakeBot()), 1, "u"))

    assert len(sweep_calls) == 1
