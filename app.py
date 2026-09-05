"""Сборка и запуск Telegram-приложения stories-bot-bothost (подзадача A2).

Точка входа: build_application() -> register_handlers() -> main().
Логика билда и регистрации обработчиков перенесена дословно из bot_host.py;
создание фоновых задач — в scheduler.post_init. Логируются только
не-секретные поля (build, путь, pid, версия схемы БД).
"""
import logging
import os

from telegram.ext import Application, CommandHandler, MessageHandler, filters

import config
import db
from scheduler import post_init, post_shutdown
from tg import (
    checknow_cmd,
    cleartoken_cmd,
    handle_text,
    list_cmd,
    silent_cmd,
    start_cmd,
    tiktok_cmd,
    tiktokreset_cmd,
    token_cmd,
    who_cmd,
)
from utils import APP_BUILD, log_dns_resolution

logger = logging.getLogger(__name__)


def build_application() -> Application:
    """Создаёт Application: токен из config + lifecycle-хуки PTB 21.x.

    post_init запускает SourceScheduler (bot_data["source_scheduler"]);
    post_shutdown останавливает его и закрывает общий httpx-клиент VK.
    """
    return (
        Application.builder()
        .token(config.API_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )


def register_handlers(app: Application) -> None:
    """Регистрирует все 9 CommandHandler + MessageHandler (дословно те же команды)."""
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("checknow", checknow_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("silent", silent_cmd))
    app.add_handler(CommandHandler("who", who_cmd))
    app.add_handler(CommandHandler("token", token_cmd))
    app.add_handler(CommandHandler("cleartoken", cleartoken_cmd))
    app.add_handler(CommandHandler("tiktok", tiktok_cmd))
    app.add_handler(CommandHandler("tiktokreset", tiktokreset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))


def main() -> None:
    """Стартовые логи, сборка приложения, регистрация обработчиков, run_polling.

    Lifecycle подключён через builder (post_init/post_shutdown): фоновые
    задачи гасятся в post_shutdown до выхода из run_polling. db.close()
    в finally оставлен как страховка — он идемпотентен (повторный close
    у sqlite3.Connection — no-op, commit на закрытом соединении глушится
    except sqlite3.Error в db.connection.close).
    """
    logger.info("Starting bot | build=%s | file=%s | pid=%s", APP_BUILD, __file__, os.getpid())
    logger.info("db user_version=%s", db.get_user_version())
    log_dns_resolution("api.telegram.org")

    app = build_application()
    register_handlers(app)

    try:
        app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
