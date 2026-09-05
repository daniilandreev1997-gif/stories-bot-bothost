"""Общая тестовая инфраструктура stories-bot-bothost (подзадача A3).

Изоляция тестов от production-окружения:
- Верхний уровень ЭТОГО модуля выполняется ДО сбора тестов, поэтому env
  (API_TOKEN, STORIES_ENCRYPTION_KEY, DB_PATH, INSTAGRAM_VIEWER_BASE_URL)
  выставляется до импорта config.py / db/, которые читают окружение при
  импорте. Значения — заглушки, НЕ реальные секреты.
- DB_PATH указывает на временный каталог процесса: даже «глобальный» conn
  из db/connection.py при импорте пакета db не трогает реальный vk_stories.db.
- Фикстура db_isolated подменяет conn во ВСЕХ модулях пакета db (в коде везде
  ``from .connection import conn`` — ссылка копируется в каждый субмодуль,
  поэтому патчить только db.connection.conn недостаточно) на отдельную
  sqlite-БД в tmp_path и применяет миграции.
- Autouse-фикстура no_network запрещает реальные HTTP-вызовы через requests.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# =======================
# ENV ДО ИМПОРТА config/db
# =======================
_TEST_ENV_DIR = tempfile.mkdtemp(prefix="sbb-tests-env-")

os.environ["API_TOKEN"] = "123456:TEST_TOKEN_NOT_REAL"
os.environ["STORIES_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["DB_PATH"] = str(Path(_TEST_ENV_DIR) / "global_isolated.db")
os.environ["INSTAGRAM_VIEWER_BASE_URL"] = ""

# =======================
# МОДУЛИ ПАКЕТА db ДЛЯ ПАТЧА conn
# =======================
# Каждый субмодуль делает ``from .connection import conn`` и держит СВОЮ
# ссылку, поэтому для полной изоляции патчится атрибут conn во всех модулях,
# где он есть (включая реэкспорт в пакете db и db.migrations, через который
# работают run_migrations/get_user_version).
_DB_CONN_MODULES = (
    "db",
    "db.connection",
    "db.migrations",
    "db.settings",
    "db.users",
    "db.dedup",
    "db.vk_tokens",
    "db.instagram",
    "db.tiktok_sessions",
    "db.tiktok_claims",
)


@pytest.fixture()
def db_isolated(tmp_path, monkeypatch):
    """Отдельная sqlite-БД на тест: conn подменён во всех модулях db.

    Создаёт файл в tmp_path, применяет PRAGMA (как db.connection._set_pragmas)
    и миграции v1..v5 через db.migrations.run_migrations() (она использует
    подменённый module-global conn). Реальный vk_stories.db не затрагивается.
    """
    db_file = tmp_path / "test_vk_stories.db"
    new_conn = sqlite3.connect(str(db_file), check_same_thread=False)
    new_conn.execute("PRAGMA journal_mode=WAL;")
    new_conn.execute("PRAGMA busy_timeout=5000;")
    new_conn.execute("PRAGMA foreign_keys=ON;")
    new_conn.execute("PRAGMA synchronous=NORMAL;")

    for mod_name in _DB_CONN_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            mod = __import__(mod_name, fromlist=["conn"])
        monkeypatch.setattr(mod, "conn", new_conn, raising=False)

    import db.migrations as db_migrations

    db_migrations.run_migrations()

    yield new_conn

    try:
        new_conn.close()
    except sqlite3.Error:
        pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Запрещает реальные сетевые вызовы requests в любом тесте.

    Тестам, которым нужен HTTP (test_vk_auth), следует явно подменять
    vk.auth.requests.post своим моком — патч из этой фикстуры тогда
    перекрывается (monkeypatch восстанавливает патчи в обратном порядке).

    Форма 2 аргументов ("dotted.path.attr", value) — target-строка резолвится
    pytest'ом; 3-аргументная форма требует реальный объект, а не строку.
    """

    def _blocked(*_args, **_kwargs):
        raise RuntimeError("Сетевые вызовы запрещены в тестах: подмени транспорт явно")

    for dotted in (
        # requests (vk/auth.py oauth-флоу и др.)
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.head",
        "requests.request",
        "requests.sessions.Session.send",
        "requests.sessions.Session.request",
        # httpx (vk/client.py: этап 6, async-транспорт)
        "httpx.get",
        "httpx.post",
        "httpx.request",
        "httpx.stream",
        "httpx.Client.send",
        "httpx.AsyncClient.send",
    ):
        monkeypatch.setattr(dotted, _blocked)
