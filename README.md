# stories-bot-bothost

Telegram-бот следит за VK-сторис и новыми публикациями TikTok, после чего отправляет найденные фото и видео в личный чат пользователя. Для TikTok бот скачивает медиа через [`yt-dlp`](requirements.txt:4), а при проблеме с загрузкой может отправить ссылку на исходную публикацию.

Пользователь задаёт VK ID и TikTok username через кнопки или команды бота. Доступ к VK можно передать токеном, а TikTok-сессию получить через опциональный вход с Playwright либо указать общий Netscape-файл cookies на сервере.

## Архитектура

Основные модули:

- [`app.py`](app.py:1) - сборка приложения, обработчики и запуск polling.
- [`runner.py`](runner.py:1) - watchdog-процесс для `app.py` и heartbeat.
- [`bot_host.py`](bot_host.py:1) - совместимый прокси на `app.main`.
- [`config.py`](config.py:1), [`crypto.py`](crypto.py:1), [`scheduler.py`](scheduler.py:1), [`utils.py`](utils.py:1) - настройки, защита данных, фоновые циклы и общие утилиты.

Слой хранения [`db/`](db/__init__.py:1) состоит из следующих модулей:

- [`connection.py`](db/connection.py:1) - SQLite-соединение, блокировка и PRAGMA.
- [`migrations.py`](db/migrations.py:1) - миграции схемы через `PRAGMA user_version`.
- [`users.py`](db/users.py:1) - пользователи и привязки VK/TikTok.
- [`settings.py`](db/settings.py:1) - настройки key/value.
- [`dedup.py`](db/dedup.py:1) - совместимость со старой дедупликацией TikTok.
- [`tiktok_claims.py`](db/tiktok_claims.py:1) - атомарный claim и статусы доставки TikTok.
- [`tiktok_sessions.py`](db/tiktok_sessions.py:1) - TikTok-сессии, логин и кэш статистики.
- [`vk_tokens.py`](db/vk_tokens.py:1) - зашифрованные VK-токены и учётные данные.
- [`instagram.py`](db/instagram.py:1) - настройки и сессии Instagram.

Пакет [`tg/`](tg/__init__.py:1) отвечает за Telegram-слой: [`handlers.py`](tg/handlers.py:1) маршрутизирует команды и сообщения, [`flows.py`](tg/flows.py:1) ведёт диалоги ввода, [`helpers.py`](tg/helpers.py:1) хранит состояния, а [`keyboards.py`](tg/keyboards.py:1) и [`messages.py`](tg/messages.py:1) формируют интерфейс бота.

В [`tiktok/`](tiktok/__init__.py:1) [`extract.py`](tiktok/extract.py:1) получает список постов через yt-dlp, [`download.py`](tiktok/download.py:1) скачивает фото и видео, [`login.py`](tiktok/login.py:1) выполняет вход через Playwright и конвертирует cookies, а [`monitoring.py`](tiktok/monitoring.py:1) связывает claim, retry, доставку и фиксацию результата.

Пакет [`vk/`](vk/__init__.py:1) содержит [`client.py`](vk/client.py:1) для VK API, [`stories.py`](vk/stories.py:1) для получения и отправки сторис и [`auth.py`](vk/auth.py:1) для прямой OAuth-авторизации.

Пакет [`instagram/`](instagram/__init__.py:1) пока содержит только [`viewer.py`](instagram/viewer.py:1): там есть контракт просмотрщика и нормализация username.

Каталог [`instagram/`](instagram/__init__.py:1) пока не получает сторис через внешний сервис: при пустом `INSTAGRAM_VIEWER_BASE_URL` функция возвращает пустой список, а реализация просмотрщика с настроенным URL ещё не добавлена.

## Требования

- Python 3.12 или новее.
- Доступ к Telegram Bot API, VK API и TikTok из среды запуска.
- Пакеты из [`requirements.txt`](requirements.txt:1): `python-telegram-bot`, `requests`, `httpx`, `yt-dlp`, `cryptography`.
- Для тестов нужен [`pytest`](requirements-dev.txt:1).
- `ffmpeg` необязателен. Если он доступен, TikTok-видео может собираться из отдельных video/audio-потоков; без него бот использует прогрессивный формат, если TikTok его отдаёт.
- Playwright необязателен и не входит в основной список зависимостей. Он нужен только для входа в TikTok по логину и паролю.

## Установка

Клонирование и переход в каталог проекта:

```powershell
# Windows PowerShell
 git clone https://github.com/daniilandreev1997-gif/stories-bot-bothost.git
 cd stories-bot-bothost
```

```bash
# Linux
 git clone https://github.com/daniilandreev1997-gif/stories-bot-bothost.git
 cd stories-bot-bothost
```

Создайте виртуальное окружение и установите зависимости.

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

```bash
# Linux
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Для разработки установите дополнительный список:

```text
python -m pip install -r requirements-dev.txt
```

Если включаете автоматический вход TikTok, отдельно установите браузер Chromium:

```text
python -m pip install playwright
python -m playwright install chromium
```

## Конфигурация

Скопируйте [`.env.example`](.env.example:1) в `.env` рядом с [`config.py`](config.py:25). Фактические значения в документацию не переносятся. Переменные окружения, заданные в системе, имеют приоритет над `.env`.

| Переменная | Обязательность | Назначение |
|---|---|---|
| `API_TOKEN` | Обязательна | Токен Telegram-бота. Без него приложение не стартует. |
| `STORIES_ENCRYPTION_KEY` | Обязательна | Ключ для шифрования пользовательских токенов, паролей и сессий. |
| `VK_TOKEN` | Опциональна | Резервный VK-токен для запросов бота; именно это имя читает [`config.py`](config.py:112). |
| `DB_PATH` | Опциональна | Путь к SQLite-файлу. |
| `CHECK_INTERVAL_SECONDS` | Опциональна | Интервал проверки VK. |
| `TOKEN_CHECK_SECONDS` | Опциональна | Интервал проверки работоспособности VK-токена. |
| `TIKTOK_CHECK_SECONDS` | Опциональна | Интервал проверки TikTok. |
| `TIKTOK_INITIAL_SYNC_GAP_SECONDS` | Опциональна | Пауза начальной синхронизации TikTok. |
| `TG_SEND_DELAY_SECONDS` | Опциональна | Пауза между отправками в Telegram. |
| `VK_API_TIMEOUT_SECONDS` | Опциональна | Таймаут запросов к VK API. |
| `HTTP_RETRIES` | Опциональна | Число повторов HTTP-запросов и операций yt-dlp. |
| `YTDLP_SOCKET_TIMEOUT_SECONDS` | Опциональна | Сетевой таймаут yt-dlp. |
| `USER_AGENT` | Опциональна | User-Agent для внешних HTTP-запросов. |
| `TIKTOK_MAX_MEDIA_PER_USER` | Опциональна | Ограничение числа медиа на пользователя; нулевое значение означает отсутствие лимита. |
| `LOG_LEVEL` | Опциональна | Уровень журналирования. |
| `VK_DIRECT_AUTH_CLIENT_ID` | Опциональна | ID приложения для подготовленного VK OAuth-флоу. |
| `VK_DIRECT_AUTH_CLIENT_SECRET` | Опциональна | Секрет приложения для того же флоу. |
| `VK_DIRECT_AUTH_SCOPE` | Опциональна | Права VK для прямой авторизации. |
| `INSTAGRAM_SESSIONS_DIR` | Опциональна | Каталог сессий Instagram для будущего интеграционного флоу. |
| `TIKTOK_COOKIES_FILE` | Опциональна | Путь к проверяемому Netscape-файлу cookies TikTok вне репозитория. |
| `TIKTOK_FFMPEG_LOCATION` | Опциональна | Путь или имя `ffmpeg`; пустое значение включает поиск в PATH. |
| `TIKTOK_POST_TIMEOUT_SECONDS` | Опциональна | Максимальное время обработки одного TikTok-поста. |
| `TIKTOK_CYCLE_TIMEOUT_SECONDS` | Опциональна | Бюджет одного цикла мониторинга TikTok. |
| `TIKTOK_RATE_LIMIT_BACKOFF_SECONDS` | Опциональна | Пауза после rate limit TikTok и cooldown перед повторным claim. |
| `TIKTOK_RETRY_BACKOFF_BASE_SECONDS` | Опциональна | Начальная пауза перед повторной загрузкой. |
| `TIKTOK_RETRY_BACKOFF_MAX_SECONDS` | Опциональна | Верхняя граница паузы повторной загрузки. |
| `TIKTOK_LOGIN_ENABLED` | Опциональна | Включает вход TikTok по логину и паролю через Playwright. |
| `TIKTOK_LOGIN_HEADLESS` | Опциональна | Запускает браузер Playwright без окна. |
| `INSTAGRAM_VIEWER_BASE_URL` | Опциональна | Базовый URL будущего внешнего просмотрщика Instagram. Пустое значение отключает его. |
| `SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS` | Опциональна | Таймаут остановки фоновых задач. |
| `VK_SOURCE_MIN_INTERVAL_SECONDS` | Опциональна | Минимальный интервал запросов к VK между пользователями. |
| `TIKTOK_SOURCE_MIN_INTERVAL_SECONDS` | Опциональна | Минимальный интервал запросов к TikTok между пользователями. |
| `RUNNER_HEARTBEAT_SECONDS` | Опциональна | Период записи файла `bot.heartbeat`. |
| `RUNNER_HEARTBEAT_STALE_SECONDS` | Опциональна | Порог, после которого watchdog считает heartbeat устаревшим. |
| `TG_MEDIA_GROUP_MAX_ITEMS` | Опциональна | Максимальное число элементов в одной Telegram-медиагруппе. |

Значения по умолчанию и проверка обязательных параметров находятся в [`config.py`](config.py:92). Логины и пароли TikTok для пользовательского входа не читаются как глобальные переменные окружения: бот сохраняет их в БД в зашифрованном виде.

## Запуск

Рекомендуемый способ запуска через watchdog:

```text
python runner.py
```

[`runner.py`](runner.py:56) запускает [`app.py`](app.py:62), следит за `bot.heartbeat`, мягко завершает зависший процесс и перезапускает его после ненулевого кода выхода. Для прямого запуска приложения используйте:

```text
python app.py
```

[`bot_host.py`](bot_host.py:1) оставлен как временный совместимый entrypoint и передаёт управление в `app.main`.

После старта [`app.py`](app.py:39) регистрирует команды `/start`, `/checknow`, `/list`, `/silent`, `/who`, `/token`, `/cleartoken`, `/tiktok`, `/tiktokreset` и текстовый обработчик. [`scheduler.py`](scheduler.py:332) создаёт циклы VK, TikTok, проверки токена и heartbeat; при остановке PTB задачи отменяются через lifecycle-хуки приложения.

## Запуск в Docker

Образ копирует проект в каталог `/app`, поэтому файл настроек в контейнере — это `/app/.env`: [`config.py`](config.py:4) читает `.env` рядом с собой, а переменные окружения, заданные в системе, имеют приоритет над файлом ([`config.py`](config.py:61) использует `os.environ.setdefault`).

### Подготовка ключа

Ключ шифрования генерируется один раз и выглядит как 44 символа urlsafe base64 (32 байта):

```text
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Скопируйте [`.env.example`](.env.example:1) в `.env` рядом с [`config.py`](config.py:1) и впишите реальные `API_TOKEN` и `STORIES_ENCRYPTION_KEY`. Пустое значение или литерал `replace_me` из шаблона уронят бот при импорте: [`config.py`](config.py:100) поднимет `ValueError` о невалидном Fernet-ключе. Само значение в ошибку не попадает — только длина и флаг плейсхолдера.

### Передача `.env` в контейнер

Способ 1 — смонтировать файл только для чтения:

```text
docker run --rm -v /path/to/stories-bot-bothost/.env:/app/.env:ro <image>
```

Способ 2 — передать через `env_file` в compose:

```text
services:
  bot:
    env_file:
      - ./.env
```

Внимание: если `STORIES_ENCRYPTION_KEY` задан через Docker `environment` или compose `environment`, это значение перекрывает `.env`: `os.environ.setdefault` из [`config.py`](config.py:61) не перезапишет уже существующую переменную. Удалите устаревшее значение из `environment`, иначе контейнер будет перезапускаться со старым ключом в crash-loop.

### Диагностика без утечки секрета

Проверить, что ключ попал в контейнер, можно командой, печатающей только факт наличия и длину значения:

```text
docker exec <container> python3 -c "import os; v=os.getenv('STORIES_ENCRYPTION_KEY',''); print({'present':bool(v),'len':len(v)})"
```

Валидный ключ даёт `len` 44. `len` 0 означает пустую переменную; в тексте ошибки при старте поле `looks_like_placeholder=True` указывает на плейсхолдер из шаблона. Значение ключа этой командой не выводится и в журнал не пишется.

## VK вход по логину и паролю

Наряду с ручным вводом токена бот может сам получать VK-токен по логину и паролю пользователя (direct password grant). Диалог реализован в [`tg/vk_login_flows.py`](tg/vk_login_flows.py:1), сам OAuth-запрос — в [`vk/auth.py`](vk/auth.py:1).

### Как работает

1. Пользователь нажимает кнопку «🔑 VK вход по логину» и присылает строку `login:password`.
2. Бот отправляет запрос к VK OAuth. Если у аккаунта включена 2FA, бот просит прислать код подтверждения.
3. Если VK показывает капчу, бот пересылает картинку и просит ввести символы с неё.
4. После успешного ответа бот сохраняет полученный токен в БД шифрованно, делает контрольный запрос и возвращает пользователя в главное меню; при проблеме присылает предупреждение.

Пароль живёт только в оперативной памяти диалога и стирается по его завершении (успех, ошибка или `/start`).

### Настройка

| Переменная | Обязательность | Назначение |
|---|---|---|
| `VK_DIRECT_AUTH_CLIENT_ID` | Обязательна для фичи | ID standalone-приложения VK. Приложение создаётся на https://dev.vk.com/ru/apps/manage (если страница недоступна — VK в техработах). |
| `VK_DIRECT_AUTH_CLIENT_SECRET` | Обязательна для фичи | Защищённый ключ того же приложения. |
| `VK_LOGIN_ENABLED` | Опциональна | Включается автоматически, когда заданы обе `VK_DIRECT_AUTH_*`. Явное значение `0` отключает кнопку независимо от них. |
| `VK_STORE_PASSWORD` | Опциональна | `0` (по умолчанию, рекомендовано) — пароль не сохраняется. `1` — логин и пароль хранятся шифрованно для тихого ре-логина. |

На bothost.ru значения `VK_DIRECT_AUTH_*` задаются через панель «Переменные окружения», после чего нажимается «Обновить из Git».

Внимание: REPLACE-семантика хранения в [`db/vk_tokens.py`](db/vk_tokens.py:1) — у пользователя одна запись, либо токен, либо логин/пароль. Сохранение пароля заменяет сохранённый токен и наоборот. При `VK_STORE_PASSWORD=0` после сброса токена VK-вход нужно пройти заново.

### Статусы ответа бота

| Статус | Что означает |
|---|---|
| `bad_password` | Неверный логин или пароль. |
| `wrong_otp` | Неверный код 2FA; даётся до 3 попыток. |
| `too_much_tries` | Превышен лимит попыток кода или капчи. |
| `need_validation` | VK запрашивает код 2FA; бот присылает маску телефона от VK. |
| `need_captcha` | VK показал капчу; бот пересылает картинку, даётся до 2 попыток. |
| `network_error` | Сетевая ошибка; состояние диалога сохраняется, можно повторить без нового ввода пароля. |

### Безопасность и риски

- Пароль никогда не пишется в журнал и не эхо-выводится в чат.
- Токен и, при `VK_STORE_PASSWORD=1`, логин/пароль шифруются Fernet-ключом `STORIES_ENCRYPTION_KEY`.
- Direct password grant — легаси-метод авторизации: VK может в любой момент ограничить или заблокировать его. Официальный статус метода не подтверждён (dev.vk.com был в техработах).
- Fallback: прежний способ — ручной ввод VK-токена — продолжает работать независимо от этой фичи.

## Тестирование

В каталоге проекта выполните:

```text
python -m pytest
```

Тесты используют временную SQLite-БД и блокируют реальные сетевые вызовы. В текущем наборе 13 тест-файлов; он покрывает сборку приложения, планировщик, runner healthcheck, шифрование, БД, VK auth и TikTok pipeline.

## Структура проекта

- [`app.py`](app.py:1), [`runner.py`](runner.py:1), [`bot_host.py`](bot_host.py:1): запуск приложения и watchdog.
- [`config.py`](config.py:1), [`crypto.py`](crypto.py:1), [`utils.py`](utils.py:1): настройки, защита данных и общие функции.
- [`db/`](db/__init__.py:1): SQLite, миграции и хранение состояния.
- [`tg/`](tg/__init__.py:1): Telegram-команды, кнопки и диалоги.
- [`vk/`](vk/__init__.py:1), [`tiktok/`](tiktok/__init__.py:1), [`instagram/`](instagram/__init__.py:1): источники и их адаптеры.
- [`tests/`](tests/conftest.py:1): автоматические тесты.

## Безопасность

`STORIES_ENCRYPTION_KEY` защищает пользовательские VK-данные, TikTok-сессии и другие чувствительные значения, которые проходят через [`crypto.py`](crypto.py:33). Сам ключ, Telegram-токен, VK-токены, пароли и cookies нельзя добавлять в Git или вставлять в README.

Файл `.env` игнорируется Git, как и SQLite-БД, сессионные файлы, каталог `ig_sessions/` и `bot.heartbeat`. Файл `TIKTOK_COOKIES_FILE` размещайте вне репозитория; его содержимое бот не выводит в журнал.

## Лицензия

В корне workspace находится [`LICENSE`](../LICENSE:1) с текстом Apache License 2.0. Для этого отдельного проекта собственного файла `LICENSE` нет.
