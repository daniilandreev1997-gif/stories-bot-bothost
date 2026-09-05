"""Планировщик фоновых источников stories-bot-bothost (Этап 6 «Планировщик и оптимизация»).

Состав:
- AsyncRateLimiter — async rate limiter на монотонных часах: между ЗАВЕРШЕНИЯМИ
  acquire() одного экземпляра проходит >= min_interval_seconds; первый вызов
  мгновенный; экземпляры независимы.
- SourceScheduler — управляемый реестр задач {name: asyncio.Task}:
  spawn_interval (цикл без перекрытий, изоляция падений), spawn (одиночная
  задача с вытеснением по имени), shutdown (graceful, с таймаутом и статусами),
  is_running.
- Фабрики-циклы vk_background_checker / tiktok_background_checker /
  token_watcher (логика и тексты сообщений сохранены; добавлена изоляция
  по-пользовательски и per-source rate limiting через AsyncRateLimiter).
- start_all / shutdown_all — модульные функции над SourceScheduler.
- post_init / post_shutdown — хуки lifecycle PTB 21.x; post_init кладёт
  SourceScheduler в application.bot_data["source_scheduler"].

Секреты не логируются; в лог попадают только имена задач и не-секретные поля.
"""
import asyncio
import logging
import os
import time
from pathlib import Path

from telegram.ext import Application

import config
import db
from tg.helpers import is_silent_mode, is_token_bad, set_token_bad_state
from tiktok import check_and_send_new_tiktoks
from vk import check_and_send_new_vk, check_token_works_for_stories

logger = logging.getLogger(__name__)

# =======================
# КОНСТАНТЫ
# =======================
# Первоначальные задержки циклов (сек): разносим старты, чтобы избежать
# одновременной вспышки сетевой активности при старте процесса.
VK_INITIAL_DELAY_SECONDS = 10
TIKTOK_INITIAL_DELAY_SECONDS = 12
TOKEN_WATCHER_INITIAL_DELAY_SECONDS = 15

# Имя ключа в application.bot_data, где хранится SourceScheduler.
SCHEDULER_BOT_DATA_KEY = "source_scheduler"
HEARTBEAT_PATH = config.BASE_DIR / "bot.heartbeat"


# =======================
# ASYNC RATE LIMITER
# =======================
class AsyncRateLimiter:
    """Async rate limiter: между завершениями acquire() >= min_interval_seconds.

    Реализация на time.monotonic() (не подвержен переводу системных часов).
    Первый вызов проходит мгновенно; последующие ждут, чтобы интервал между
    моментами ВОЗВРАТА из acquire() был >= min_interval_seconds. Экземпляры
    полностью независимы (состояние — только в self).
    """

    def __init__(self, min_interval_seconds: float):
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be >= 0")
        self._min_interval = float(min_interval_seconds)
        self._next_allowed = 0.0  # момент (monotonic), начиная с которого можно пускать
        self._lock = asyncio.Lock()  # сериализация конкурентных acquire()

    async def acquire(self) -> None:
        """Ждёт (при необходимости) и резервирует следующий слот.

        Под asyncio.Lock: конкурентные вызовы сериализуются, интервал
        соблюдается между ЗАВЕРШЕНИЯМИ acquire() даже при гонках.
        """
        async with self._lock:
            now = time.monotonic()
            wait_seconds = self._next_allowed - now
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
                now = time.monotonic()
            # Следующий слот отсчитывается от текущего момента (конца ожидания).
            self._next_allowed = max(self._next_allowed, now) + self._min_interval


# =======================
# SOURCE SCHEDULER
# =======================
class SourceScheduler:
    """Реестр управляемых фоновых задач {name: asyncio.Task}.

    - spawn_interval: бесконечный цикл; интервал отсчитывается от ЗАВЕРШЕНИЯ
      coro_factory() (нет перекрытий); исключение не убивает цикл (лог + retry).
    - spawn: одиночная задача по имени; повтор имени вытесняет старую.
    - shutdown: cancel всех + gather под wait_for(timeout); возвращает
      {name: bool} — True, если задача завершилась без «зависания» при отмене.
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}

    # --- регистрация -----------------------------------------------------

    def spawn_interval(self, name: str, coro_factory, *, interval_seconds: float,
                       initial_delay: float = 0.0) -> asyncio.Task:
        """Запускает именованный бесконечный цикл coro_factory().

        Цикл: initial_delay -> [coro_factory() -> sleep(interval)]*. Интервал
        от завершения coro_factory — нет перекрытия; исключение корутины
        логируется (logger.exception) и не убивает цикл.
        """
        return self.spawn(
            name,
            self._make_interval_loop(name, coro_factory, interval_seconds),
            initial_delay=initial_delay,
        )

    def spawn(self, name: str, coro_factory, *, initial_delay: float = 0.0) -> asyncio.Task:
        """Запускает одиночную задачу под именем name.

        Повтор имени: старая задача cancel() + spawn новой. Отмена старой
        задачи выполняется без ожидания, чтобы spawn оставался синхронным.
        """
        old = self._tasks.get(name)
        if old is not None and not old.done():
            old.cancel()

        async def _runner():
            if initial_delay > 0:
                await asyncio.sleep(initial_delay)
            await coro_factory()

        task = asyncio.get_running_loop().create_task(_runner(), name=name)
        self._tasks[name] = task
        return task

    # --- внутренний цикл ---------------------------------------------------

    @staticmethod
    def _make_interval_loop(name: str, coro_factory, interval_seconds: float):
        """Создаёт корутину-цикл: loop { try coro; sleep(interval) }."""

        async def _loop():
            while True:
                try:
                    await coro_factory()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Ошибка цикла %r: %r", name, exc)
                await asyncio.sleep(interval_seconds)

        return _loop

    # --- статус ----------------------------------------------------------

    def is_running(self, name: str) -> bool:
        """True, если задача name существует, не завершена и не отменена."""
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def task_names(self) -> list[str]:
        """Имена всех зарегистрированных задач (для логов/диагностики)."""
        return list(self._tasks.keys())

    # --- shutdown ----------------------------------------------------------

    async def shutdown(self, *, timeout: float = 10.0) -> dict[str, bool]:
        """Graceful shutdown всех задач; НЕ бросает исключений.

        Возвращает {name: bool}: True — задача завершилась при отмене без
        «зависания» (done и не в состоянии ожидания внешнего world), False —
        не успела завершиться за timeout (зависшая). После возврата
        is_running(name) == False для всех имен.
        """
        statuses: dict[str, bool] = {}
        if not self._tasks:
            return statuses

        tasks = list(self._tasks.values())

        # Дать freshly-created tasks один цикл event loop. Это гарантирует,
        # что отмена попадёт в тело coroutine и корректно обрабатывается его
        # собственным CancelledError-handler'ом, а не отменяет задачу до старта.
        await asyncio.sleep(0)
        for task in tasks:
            if not task.done():
                task.cancel()

        # asyncio.wait НЕ каскадно отменяет pending-задачи по таймауту
        # (в отличие от gather под wait_for): «упрямая» задача, глотающая
        # CancelledError, остаётся незавершённой -> честный статус False.
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        # Повторная отмена позволяет корректно завершить задачу, которая
        # проглотила первую отмену и теперь ждёт внутри cleanup. Статус уже
        # фиксируем как False: она не завершилась в пределах timeout.
        for task in pending:
            task.cancel()

        # Статус: True — задача завершилась (в т.ч. отменой) в пределах
        # таймаута; False — «зависла» (не отработала отмену за timeout).
        for name, task in self._tasks.items():
            is_done = task in done
            statuses[name] = is_done
            if not is_done:
                logger.warning(
                    "Задача %r не завершилась за %s c (отмена не обработана)",
                    name, timeout,
                )

        # Реестр очищается: is_running -> False независимо от фактического
        # завершения (зависшие задачи продолжают жить, но больше не управляются).
        self._tasks.clear()
        return statuses


# =======================
# ФАБРИКИ-ЦИКЛЫ
# =======================
async def vk_background_checker(app: Application):
    """Цикл VK: раз в CHECK_INTERVAL_SECONDS проверяет сторис всех пользователей.

    Изоляция: каждый пользователь обрабатывается в собственном try/except —
    падение одного не прерывает остальных. Перед КАЖДЫМ пользователем —
    await vk_rate_limiter.acquire() (интервал config.VK_SOURCE_MIN_INTERVAL_SECONDS).
    """
    vk_rate_limiter = AsyncRateLimiter(config.VK_SOURCE_MIN_INTERVAL_SECONDS)
    while True:
        try:
            users = db.load_vk_users()
            if users and not is_token_bad():
                for tg_id, vk_id, last_story_id in users:
                    await vk_rate_limiter.acquire()
                    try:
                        await check_and_send_new_vk(app, tg_id, vk_id, last_story_id)
                    except Exception as exc:
                        logger.exception("Ошибка проверки VK пользователя tg_id=%s: %r", tg_id, exc)
        except Exception as exc:
            logger.exception("Ошибка фоновой проверки VK: %r", exc)

        await asyncio.sleep(config.CHECK_INTERVAL_SECONDS)


async def tiktok_background_checker(app: Application):
    """Цикл TikTok: раз в TIKTOK_CHECK_SECONDS проверяет новые посты всех пользователей.

    Изоляция по-пользовательски + per-source rate limiting
    (config.TIKTOK_SOURCE_MIN_INTERVAL_SECONDS).
    """
    tiktok_rate_limiter = AsyncRateLimiter(config.TIKTOK_SOURCE_MIN_INTERVAL_SECONDS)
    while True:
        try:
            users = db.load_tiktok_users()
            for tg_id, username in users:
                await tiktok_rate_limiter.acquire()
                try:
                    await check_and_send_new_tiktoks(app, tg_id, username)
                except Exception as exc:
                    logger.exception("Ошибка проверки TikTok пользователя tg_id=%s: %r", tg_id, exc)
        except Exception as exc:
            logger.exception("Ошибка фоновой проверки TikTok: %r", exc)

        await asyncio.sleep(config.TIKTOK_CHECK_SECONDS)


async def token_watcher(app: Application):
    """Цикл: раз в TOKEN_CHECK_SECONDS проверяет токен и уведомляет о смене состояния.

    Логика и тексты сообщений сохранены дословно из предыдущей версии.
    """
    last_state = db.get_setting("token_state").strip() or "ok"

    while True:
        try:
            users = db.load_vk_users()
            if not users:
                await asyncio.sleep(config.TOKEN_CHECK_SECONDS)
                continue

            test_vk_id = users[0][1]
            ok, reason = await check_token_works_for_stories(test_vk_id)
            silent = is_silent_mode()

            if ok:
                set_token_bad_state(False)
                if last_state != "ok" and not silent:
                    for tg_id, _, _ in users:
                        try:
                            await app.bot.send_message(chat_id=tg_id, text="✅ VK токен снова работает.")
                        except Exception:
                            pass
                last_state = "ok"
            else:
                set_token_bad_state(True, reason)
                if last_state != "bad":
                    msg = (
                        "❌ VK токен не работает.\n"
                        f"{reason}\n\n"
                        "Проверка VK сторис остановлена. Пришли новый /token"
                    )
                    for tg_id, _, _ in users:
                        try:
                            await app.bot.send_message(chat_id=tg_id, text=msg)
                        except Exception:
                            pass
                last_state = "bad"

        except Exception as exc:
            logger.exception("Ошибка token_watcher: %r", exc)

        await asyncio.sleep(config.TOKEN_CHECK_SECONDS)


# =======================
# START ALL / SHUTDOWN ALL
# =======================
async def heartbeat_writer() -> None:
    """Пишет heartbeat сразу и затем с заданным интервалом атомарно."""
    heartbeat_tmp = HEARTBEAT_PATH.with_name(f"{HEARTBEAT_PATH.name}.tmp")
    while True:
        try:
            HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_tmp.write_text(str(time.time()), encoding="utf-8")
            os.replace(heartbeat_tmp, HEARTBEAT_PATH)
        except asyncio.CancelledError:
            raise
        except OSError:
            logger.exception("Не удалось записать heartbeat: %s", HEARTBEAT_PATH)
        await asyncio.sleep(config.RUNNER_HEARTBEAT_SECONDS)


def start_all(app: Application) -> SourceScheduler:
    """Создаёт SourceScheduler и запускает три цикла источников и heartbeat.

    Имена задач: vk_checker / tiktok_checker / token_watcher / heartbeat.
    Интервалы и initial delay — из config и констант модуля.
    """
    scheduler = SourceScheduler()
    scheduler.spawn_interval(
        "vk_checker",
        lambda: vk_background_checker(app),
        interval_seconds=config.CHECK_INTERVAL_SECONDS,
        initial_delay=VK_INITIAL_DELAY_SECONDS,
    )
    scheduler.spawn_interval(
        "tiktok_checker",
        lambda: tiktok_background_checker(app),
        interval_seconds=config.TIKTOK_CHECK_SECONDS,
        initial_delay=TIKTOK_INITIAL_DELAY_SECONDS,
    )
    scheduler.spawn_interval(
        "token_watcher",
        lambda: token_watcher(app),
        interval_seconds=config.TOKEN_CHECK_SECONDS,
        initial_delay=TOKEN_WATCHER_INITIAL_DELAY_SECONDS,
    )
    scheduler.spawn("heartbeat", heartbeat_writer)
    return scheduler


async def shutdown_all(scheduler: SourceScheduler, *, timeout: float | None = None) -> dict[str, bool]:
    """Останавливает все задачи scheduler'а; таймаут по умолчанию — из config."""
    if timeout is None:
        timeout = config.SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS

    # Нормальный bound method получает self автоматически. Такой вызов также
    # поддерживает тестовые/интеграционные monkeypatch-функции, объявленные
    # без self (они остаются обычной функцией на уровне класса).
    import inspect

    class_method = type(scheduler).shutdown
    parameters = list(inspect.signature(class_method).parameters.values())
    if parameters and parameters[0].name in {"self", "cls"}:
        return await scheduler.shutdown(timeout=timeout)
    return await class_method(timeout=timeout)


# =======================
# LIFECYCLE HOOKS (PTB 21.x)
# =======================
async def post_init(application: Application):
    """PTB post_init: создаёт SourceScheduler и кладёт его в bot_data.

    Задачи запускаются в работающем event loop приложения (в отличие от
    старой версии на loop.create_task до старта loop).
    """
    scheduler = start_all(application)
    application.bot_data[SCHEDULER_BOT_DATA_KEY] = scheduler
    logger.info(
        "Планировщик запущен | tasks=%s | vk_min_interval=%s | tiktok_min_interval=%s",
        sorted(scheduler.task_names()),
        config.VK_SOURCE_MIN_INTERVAL_SECONDS,
        config.TIKTOK_SOURCE_MIN_INTERVAL_SECONDS,
    )


async def post_shutdown(application: Application):
    """PTB post_shutdown: останавливает планировщик из bot_data (если есть)."""
    scheduler = application.bot_data.get(SCHEDULER_BOT_DATA_KEY)
    if scheduler is not None:
        statuses = await shutdown_all(scheduler)
        logger.info("Планировщик остановлен | statuses=%s", statuses)
    else:
        logger.info("post_shutdown: планировщик в bot_data не найден (нечего останавливать)")

    # VK transport is process-global and must be closed after source tasks stop.
    from vk.client import close_http_client

    await close_http_client()
