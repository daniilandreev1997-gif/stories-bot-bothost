"""RED-тесты утилит этапа 6: is_heartbeat_stale (healthcheck runner) и
chunk_media_groups (лимит Telegram 10 медиа на media-group).

Контракт (реализуется следующим code-этапом в utils.py):
- is_heartbeat_stale(path, stale_seconds, now=None):
    path отсутствует -> True; mtime старше stale_seconds относительно
    now (default time.time()) -> True; иначе False.
- chunk_media_groups(items, max_items=10): разбивка на чанки по max_items;
    [] для пустого списка; порядок сохраняется; вход не мутируется.

Файлы: только tmp_path; никаких реальных subprocess; сеть не используется.
"""
import os
import time
from pathlib import Path

import pytest

from utils import chunk_media_groups, is_heartbeat_stale


# =======================
# is_heartbeat_stale
# =======================
class TestIsHeartbeatStale:
    def test_heartbeat_missing_is_stale(self, tmp_path):
        """Отсутствующий файл heartbeat -> stale=True."""
        missing = tmp_path / "heartbeatmissing.file"
        assert is_heartbeat_stale(str(missing), 300) is True
        assert is_heartbeat_stale(missing, 300) is True  # Path тоже принимается

    def test_heartbeat_fresh_not_stale(self, tmp_path):
        """Свежий heartbeat (mtime = now) -> False."""
        hb = tmp_path / "heartbeat.file"
        hb.write_text("alive", encoding="utf-8")
        now = time.time()
        os.utime(hb, (now, now))
        assert is_heartbeat_stale(str(hb), 300, now=now + 1) is False
        assert is_heartbeat_stale(hb, 300, now=now) is False

    def test_heartbeat_old_is_stale(self, tmp_path):
        """mtime старше stale_seconds -> True; на границе (ровно stale) -> True.

        mtime == now - stale_seconds означает, что возраст >= stale_seconds.
        """
        hb = tmp_path / "heartbeat.file"
        hb.write_text("alive", encoding="utf-8")
        now = 1_700_000_000.0

        # Возраст 400 c при stale=300 -> stale.
        os.utime(hb, (now - 400, now - 400))
        assert is_heartbeat_stale(str(hb), 300, now=now) is True

        # Граница: возраст ровно 300 c -> stale (>=).
        os.utime(hb, (now - 300, now - 300))
        assert is_heartbeat_stale(str(hb), 300, now=now) is True

        # Возраст 299 c -> не stale.
        os.utime(hb, (now - 299, now - 299))
        assert is_heartbeat_stale(str(hb), 300, now=now) is False

    def test_heartbeat_uses_real_time_by_default(self, tmp_path):
        """Без now используется time.time(): свежий файл не stale, старый — stale."""
        hb = tmp_path / "heartbeat.file"
        hb.write_text("alive", encoding="utf-8")

        # Свежий: mtime = текущее время.
        now = time.time()
        os.utime(hb, (now, now))
        assert is_heartbeat_stale(str(hb), 300) is False

        # Старый: mtime сдвинут на 1000 c назад.
        os.utime(hb, (now - 1000, now - 1000))
        assert is_heartbeat_stale(str(hb), 300) is True


# =======================
# chunk_media_groups
# =======================
class TestChunkMediaGroups:
    def test_chunk_media_groups_empty(self):
        assert chunk_media_groups([]) == []

    def test_chunk_media_groups_size_10(self):
        """25 элементов -> чанки [10, 10, 5]; порядок сохранён."""
        items = [f"item_{i}" for i in range(25)]
        chunks = chunk_media_groups(items)
        assert [len(c) for c in chunks] == [10, 10, 5]
        assert [item for chunk in chunks for item in chunk] == items

    def test_chunk_media_groups_custom_max(self):
        """max_items=3, 7 элементов -> [3, 3, 1]; порядок сохранён."""
        items = [1, 2, 3, 4, 5, 6, 7]
        chunks = chunk_media_groups(items, max_items=3)
        assert chunks == [[1, 2, 3], [4, 5, 6], [7]]
        assert [item for chunk in chunks for item in chunk] == items

    def test_chunk_media_groups_does_not_mutate_input(self):
        """Входной список не мутируется."""
        items = [1, 2, 3, 4, 5]
        snapshot = list(items)
        chunks = chunk_media_groups(items, max_items=2)
        assert items == snapshot
        assert chunks == [[1, 2], [3, 4], [5]]
        assert chunks is not items

    def test_chunk_media_groups_exact_multiple(self):
        """Длина кратна max_items: без хвостового пустого чанка."""
        items = list(range(20))
        chunks = chunk_media_groups(items, max_items=10)
        assert chunks == [items[:10], items[10:]]

    def test_chunk_media_groups_single_item(self):
        """Один элемент -> один чанк из одного элемента."""
        assert chunk_media_groups(["only"]) == [["only"]]

    def test_chunk_media_groups_default_max_is_10(self):
        """Default max_items=10 (Telegram limit media-group)."""
        items = list(range(11))
        chunks = chunk_media_groups(items)
        assert len(chunks[0]) == 10
        assert len(chunks[1]) == 1


# =======================
# config.py: новые настройки этапа 6 (все с default)
# =======================
class TestConfigNewSettings:
    """Новые настройки config.py: существуют и имеют безопасные defaults.

    Считываются при импорте config, поэтому проверяем факт наличия атрибутов
    и дефолтные значения (env-заглушки conftest не задают эти ключи).
    """

    def test_scheduler_shutdown_timeout_default(self):
        import config

        assert isinstance(config.SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS, int)
        assert config.SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS > 0

    def test_source_min_interval_defaults(self):
        import config

        assert isinstance(config.VK_SOURCE_MIN_INTERVAL_SECONDS, float)
        assert isinstance(config.TIKTOK_SOURCE_MIN_INTERVAL_SECONDS, float)
        assert config.VK_SOURCE_MIN_INTERVAL_SECONDS > 0
        assert config.TIKTOK_SOURCE_MIN_INTERVAL_SECONDS > 0

    def test_runner_heartbeat_defaults(self):
        import config

        assert isinstance(config.RUNNER_HEARTBEAT_SECONDS, int)
        assert isinstance(config.RUNNER_HEARTBEAT_STALE_SECONDS, int)
        assert config.RUNNER_HEARTBEAT_SECONDS > 0
        assert config.RUNNER_HEARTBEAT_STALE_SECONDS > 0
        assert config.RUNNER_HEARTBEAT_STALE_SECONDS > config.RUNNER_HEARTBEAT_SECONDS
