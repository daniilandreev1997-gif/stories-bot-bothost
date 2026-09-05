"""RED-тесты нового API scheduler.py (Этап 6 «Планировщик и оптимизация»).

Контракт (реализуется следующим code-этапом):
- AsyncRateLimiter(min_interval_seconds): acquire() без ожидания первый раз;
  между возвратами acquire() одного экземпляра >= min_interval_seconds;
  независимые экземпляры не влияют друг на друга.
- SourceScheduler: spawn_interval / spawn / shutdown / is_running —
  изоляция падения источника, интервал от ЗАВЕРШЕНИЯ coro_factory (нет
  перекрытия), graceful shutdown с таймаутом и статусами.
- start_all(app) -> SourceScheduler с управляемыми задачами
  {vk_checker, tiktok_checker, token_watcher}; shutdown_all(scheduler).

Стиль проекта: pytest-asyncio не используется — async-код запускается через
asyncio.run() в синхронных тестах (как в tests/test_app_smoke.py). Сети нет;
времязависимые проверки — с запасом (0.9-коэффициент/верхние границы),
чтобы не flakiness. Секретов нет.
"""
import asyncio
import logging
import time

import pytest

# Импорт API планировщика.
from scheduler import AsyncRateLimiter, SourceScheduler, shutdown_all, start_all


# =======================
# AsyncRateLimiter
# =======================
class TestAsyncRateLimiter:
    def test_rate_limiter_first_acquire_immediate(self):
        """Первый acquire() проходит без ожидания (< 0.05 c при интервале 0.1)."""

        async def scenario():
            limiter = AsyncRateLimiter(0.1)
            start = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - start

        elapsed = asyncio.run(scenario())
        assert elapsed < 0.05

    def test_rate_limiter_enforces_min_interval(self):
        """Второй acquire() ждёт >= min_interval*0.9 (запас против flakiness)."""

        async def scenario():
            limiter = AsyncRateLimiter(0.1)
            await limiter.acquire()
            start = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - start

        elapsed = asyncio.run(scenario())
        assert elapsed >= 0.1 * 0.9

    def test_rate_limiter_independent_instances(self):
        """Два экземпляра лимитеров не ждут друг друга."""

        async def scenario():
            a = AsyncRateLimiter(0.1)
            b = AsyncRateLimiter(0.1)
            await a.acquire()
            await b.acquire()

            start = time.monotonic()
            await asyncio.gather(a.acquire(), b.acquire())
            return time.monotonic() - start

        elapsed = asyncio.run(scenario())
        # Оба просто ждали СВОЙ минимальный интервал (~0.1), а не 2 интервала.
        assert elapsed < 0.18

    # Негативный тест сигнатуры: конструктор принимает ровно один позиционный аргумент.
    def test_rate_limiter_constructor_signature(self):
        AsyncRateLimiter(0.05)
        with pytest.raises(TypeError):
            AsyncRateLimiter()


# =======================
# SourceScheduler
# =======================
class TestSourceScheduler:
    def test_scheduler_isolates_failing_source(self, caplog):
        """Падение источника A не убивает цикл B; исключение A залогировано.

        Гарантии: задача A жива до shutdown; после shutdown обе завершены.
        """
        ticks = {"b": 0}

        async def failing_factory():
            raise RuntimeError("source A always fails")

        async def counting_factory():
            ticks["b"] += 1

        async def scenario():
            scheduler = SourceScheduler()
            task_a = scheduler.spawn_interval(
                "a_failing", failing_factory, interval_seconds=0.01
            )
            scheduler.spawn_interval("b_counting", counting_factory, interval_seconds=0.01)

            await asyncio.sleep(0.15)
            alive_before = not task_a.done()  # исключение не убило цикл A
            statuses = await scheduler.shutdown(timeout=2)
            return alive_before, statuses

        with caplog.at_level(logging.ERROR, logger="scheduler"):
            alive_before, statuses = asyncio.run(scenario())

        assert ticks["b"] >= 2
        assert alive_before is True
        # Статус: цикл A завершился при отмене БЕЗ необработанного исключения,
        # т.е. CancelledError не «съеден» -> задача завершена без cancel-пометки.
        # Для managed-цикла корректное поведение: True (задача done, без исключений).
        assert statuses["a_failing"] is True
        assert statuses["b_counting"] is True
        # Исключение источника A попало в лог.
        assert any("a_failing" in rec.message or "RuntimeError" in rec.message
                   for rec in caplog.records)

    def test_scheduler_shutdown_cancels_tasks(self):
        """После shutdown все задачи cancelled/done, is_running -> False."""

        async def endless_factory():
            while True:
                await asyncio.sleep(3600)

        async def scenario():
            scheduler = SourceScheduler()
            scheduler.spawn_interval("loop1", endless_factory, interval_seconds=3600)
            scheduler.spawn("loop2", endless_factory)
            running_before = (
                scheduler.is_running("loop1") and scheduler.is_running("loop2")
            )
            statuses = await scheduler.shutdown(timeout=2)
            still_running = (
                scheduler.is_running("loop1") or scheduler.is_running("loop2")
            )
            return running_before, statuses, still_running

        running_before, statuses, still_running = asyncio.run(scenario())
        assert running_before is True
        assert statuses == {"loop1": True, "loop2": True}
        assert still_running is False

    def test_scheduler_shutdown_timeout_does_not_hang(self):
        """Задача «сопротивляется» отмене (глотает CancelledError) — shutdown
        не висит дольше таймаута, возвращает статус этой задачи False.
        """

        async def stubborn_factory():
            # taskshield: проглатывает CancelledError и висит в sleep(10)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(10)  # НЕ re-raise — вечное сопротивление

        async def scenario():
            scheduler = SourceScheduler()
            scheduler.spawn("stubborn", stubborn_factory)
            start = time.monotonic()
            statuses = await scheduler.shutdown(timeout=0.2)
            return time.monotonic() - start, statuses

        # Общий страховочный таймаут теста: 2 секунды.
        elapsed, statuses = asyncio.run(asyncio.wait_for(scenario(), timeout=2.0))
        assert elapsed < 2.0
        assert statuses["stubborn"] is False

    def test_scheduler_no_overlap(self):
        """Интервал отсчитывается от ЗАВЕРШЕНИЯ coro_factory: нет перекрытия.

        coro_factory спит 0.05, интервал 0.01; за 0.15 c стартов <= 4 прогонов
        и max_concurrent == 1.
        """
        state = {"runs": 0, "concurrent": 0, "max_concurrent": 0}

        async def slow_factory():
            state["concurrent"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["concurrent"])
            try:
                await asyncio.sleep(0.05)
            finally:
                state["concurrent"] -= 1
            state["runs"] += 1

        async def scenario():
            scheduler = SourceScheduler()
            scheduler.spawn_interval("slow", slow_factory, interval_seconds=0.01)
            await asyncio.sleep(0.15)
            await scheduler.shutdown(timeout=2)

        asyncio.run(scenario())
        assert state["runs"] <= 4
        assert state["max_concurrent"] == 1

    def test_spawn_replaces_same_name(self):
        """Повтор spawn с тем же именем: старая задача отменяется, новая живёт."""

        async def wait_forever():
            while True:
                await asyncio.sleep(3600)

        async def scenario():
            scheduler = SourceScheduler()
            old_task = scheduler.spawn("worker", wait_forever)
            await asyncio.sleep(0.01)  # дать старой задаче стартовать
            new_task = scheduler.spawn("worker", wait_forever)
            await asyncio.sleep(0.05)  # передать loop обработку отмены старой задачи
            new_alive = not new_task.done()
            replaced_and_running = scheduler.is_running("worker")
            statuses = await scheduler.shutdown(timeout=2)
            return old_task, new_task, new_alive, replaced_and_running, statuses

        old_task, new_task, new_alive, replaced_and_running, statuses = asyncio.run(scenario())
        assert old_task is not new_task
        assert old_task.cancelled(), "старая задача с тем же именем отменена"
        assert new_alive is True, "новая задача жива до shutdown"
        assert replaced_and_running is True
        assert statuses == {"worker": True}

    def test_shutdown_status_value_is_true_when_task_finished_naturally(self):
        """Задача, завершившаяся сама (не отменой) до shutdown, даёт True."""

        async def self_finishing_factory():
            await asyncio.sleep(0)  # завершается мгновенно

        async def scenario():
            scheduler = SourceScheduler()
            scheduler.spawn("quick", self_finishing_factory)
            await asyncio.sleep(0.05)
            return await scheduler.shutdown(timeout=2)

        statuses = asyncio.run(scenario())
        assert statuses["quick"] is True


# =======================
# start_all / shutdown_all
# =======================
class TestStartAllShutdownAll:
    def _fake_app(self):
        """Мок Application (как в контракте: Mock с .bot)."""
        from unittest.mock import Mock

        app = Mock()
        app.bot = Mock()
        return app

    def test_start_all_creates_managed_tasks(self, monkeypatch):
        """start_all создаёт scheduler с четырьмя управляемыми задачами;
        shutdown_all завершает все.

        Циклы-фабрики monkeypatch'атся на фейковые (бесконечный сон), чтобы
        не запускать реальные VK/TikTok проверки (сеть в тестах запрещена).
        """
        import scheduler as scheduler_module

        async def fake_vk_cycle(app):
            while True:
                await asyncio.sleep(3600)

        async def fake_tiktok_cycle(app):
            while True:
                await asyncio.sleep(3600)

        async def fake_token_cycle(app):
            while True:
                await asyncio.sleep(3600)

        monkeypatch.setattr(scheduler_module, "vk_background_checker", fake_vk_cycle)
        monkeypatch.setattr(scheduler_module, "tiktok_background_checker", fake_tiktok_cycle)
        monkeypatch.setattr(scheduler_module, "token_watcher", fake_token_cycle)

        async def scenario():
            scheduler = start_all(self._fake_app())
            try:
                running = (
                    scheduler.is_running("vk_checker")
                    and scheduler.is_running("tiktok_checker")
                    and scheduler.is_running("token_watcher")
                    and scheduler.is_running("heartbeat")
                )
            finally:
                statuses = await shutdown_all(scheduler, timeout=2)

            return scheduler, running, statuses

        scheduler, running, statuses = asyncio.run(scenario())
        assert running is True
        # Три управляемые задачи присутствуют (проверка через статусы shutdown).
        assert set(statuses.keys()) == {"vk_checker", "tiktok_checker", "token_watcher", "heartbeat"}
        assert all(statuses.values()), "все задачи должны завершиться при shutdown"
        # После shutdown ничего не крутится.
        assert not (
            scheduler.is_running("vk_checker")
            or scheduler.is_running("tiktok_checker")
            or scheduler.is_running("token_watcher")
            or scheduler.is_running("heartbeat")
        )

    def test_shutdown_all_default_timeout_from_config(self, monkeypatch):
        """shutdown_all без явного timeout берёт config.SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS."""
        import config
        import scheduler as scheduler_module

        seen = {"timeout": None}

        async def fake_shutdown(timeout=10.0):
            seen["timeout"] = timeout
            return {}

        monkeypatch.setattr(config, "SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", 3)
        # Подменяем метод на классе (экземпляра ещё нет).
        monkeypatch.setattr(scheduler_module.SourceScheduler, "shutdown", fake_shutdown)

        async def scenario():
            scheduler = scheduler_module.SourceScheduler()
            return await shutdown_all(scheduler)

        asyncio.run(scenario())
        assert seen["timeout"] == 3

    def test_start_all_returns_source_scheduler(self, monkeypatch):
        """start_all возвращает именно SourceScheduler."""
        import scheduler as scheduler_module

        async def fake_cycle(app):
            while True:
                await asyncio.sleep(3600)

        monkeypatch.setattr(scheduler_module, "vk_background_checker", fake_cycle)
        monkeypatch.setattr(scheduler_module, "tiktok_background_checker", fake_cycle)
        monkeypatch.setattr(scheduler_module, "token_watcher", fake_cycle)

        async def scenario():
            scheduler = start_all(self._fake_app())
            try:
                return type(scheduler).__name__
            finally:
                await shutdown_all(scheduler, timeout=2)

        assert asyncio.run(scenario()) == "SourceScheduler"
