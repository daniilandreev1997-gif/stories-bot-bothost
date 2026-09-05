# Дизайн: VK-вход по логину/паролю (2FA + капча) и per-user токен

Статус: дизайн v1 (реализация не начата). Артефакт-источник контракта: [`vk_direct_auth()`](../vk/auth.py:27).
Дата: 2026-09-05. Ограничение скоупа: код не менялся в рамках этого дока; меняются только
`config.py`, `tg/*`, `.env.example`, `tests/` — `vk/auth.py`, `db/vk_tokens.py`, `vk/stories.py`,
`db/migrations.py` не трогаются.

---

## 1. Цели и нецели

### Цели

1. Вход в VK прямо из Telegram-бота: пользователь присылает `логин:пароль`, бот проходит
   direct-auth через [`vk_direct_auth()`](../vk/auth.py:27) (grant_type=password, oauth.vk.com).
2. Поддержка подтверждения 2FA: ветка `need_validation` → запрос кода → повторный вызов с `code`.
3. Поддержка капчи: ветка `need_captcha` → отправка картинки капчи → повторный вызов с
   `captcha_sid` + `captcha_key`.
4. Per-user токен: после успеха токен сохраняется шифрованно в `vk_user_tokens`
   ([`save_vk_user_token()`](../db/vk_tokens.py:28)) — «работа бота по токену» без ручного `Token VK`.
5. Диалоговый FSM-механизм повторного ввода (код/капча) поверх существующих wait-states.

### Нецели (v1)

| Нецель | Причина / отложено |
| --- | --- |
| VK ID OAuth (id.vk.com, PKCE) | Требует redirect-URI и web-view; direct-auth уже реализован и протестирован. |
| Куки → токен (v1-конвертация) | Не нужен: [`vk_direct_auth()`](../vk/auth.py:27) возвращает токен напрямую. |
| Пер-юзерные истории в мониторинге | [`get_any_active_vk_token()`](../db/vk_tokens.py:83) остаётся глобальным — не ломаем мониторинг (см. §7.3). |
| Молчаливый re-auth по сохранённому паролю | Зависит от хранения пароля; см. §7.2 и §10. |
| Хранение пароля по умолчанию | См. решение ниже. |

### Решение: хранение пароля (`VK_STORE_PASSWORD`, default `0`)

**V1 хранит только токен. Пароль по умолчанию НЕ хранится.**

Обоснование:

- Пароль нужен v1 только на время одного диалога (вход + повторные вызовы с `code`/`captcha_key`);
  после `ok` он больше не нужен — при повторном входе пользователь введёт его снова.
- Токен пересохраняем: перезапись токена не требует пароля, значит цикл «вход → токен» замкнут.
- Схема `vk_user_tokens` хранит **одну строку на `tg_id`** с взаимоисключающим `token_kind`:
  [`save_vk_user_token()`](../db/vk_tokens.py:28) пишет `kind='token'` и затирает `login_enc`/`password_enc`,
  [`save_vk_user_password()`](../db/vk_tokens.py:40) пишет `kind='password'` и затирает `token_enc`.
  Долговременное хранение пароля **и** токена одновременно в текущей схеме невозможно
  (потребовалась бы миграция) — это осознанное ограничение v1, см. §7.2.
- Минимизация поверхности утечки: пароль живёт только в RAM диалога и никогда не логируется.

Опция для совместимости с ТЗ и будущим re-auth: `VK_STORE_PASSWORD` в [`config.py`](../config.py).

- `0` (default): пароль не сохраняется вовсе; после `ok` — только [`save_vk_user_token()`](../db/vk_tokens.py:28).
- `1`: после `ok` вызывается [`save_vk_user_password()`](../db/vk_tokens.py:40) **сразу перед**
  [`save_vk_user_token()`](../db/vk_tokens.py:28). Из-за REPLACE-семантики итоговая строка —
  `kind='token'`: парольная запись краткоживуща и замещается токеном. Практический смысл флага —
  точка расширения для будущего re-auth (миграция будет нужна) и сохранение пароля в edge-кейсе,
  когда сохранить токен не удалось (ошибка БД). Рекомендуемое значение — `0`.

---

## 2. Гейт-флаг `VK_LOGIN_ENABLED`

Гейт = **обе** переменные непустые:

```python
# config.py (эскиз, не менять код в рамках дока)
VK_LOGIN_ENABLED = (
    os.getenv("VK_LOGIN_ENABLED", "1").strip() != "0"
    and bool(VK_DIRECT_AUTH_CLIENT_ID)
    and bool(VK_DIRECT_AUTH_CLIENT_SECRET)
)
```

- [`VK_DIRECT_AUTH_CLIENT_ID`](../config.py:140) / [`VK_DIRECT_AUTH_CLIENT_SECRET`](../config.py:141)
  уже читаются из env (сейчас пустые по умолчанию), [`VK_DIRECT_AUTH_SCOPE`](../config.py:142) = `'stories'`.
- Явный env-оверрайд `VK_LOGIN_ENABLED=0` принудительно выключает флоу даже при заполненных кредах
  (симметрично [`TIKTOK_LOGIN_ENABLED`](../config.py:181)).
- Поведение при выключенном гейте: кнопка «VK вход» отвечает текстом
  «🔒 Вход по VK логину/паролю временно недоступен…» и wait-state НЕ ставится
  (образец — [`tiktok_login_disabled_text()`](../tg/messages.py:234)).
- Значения `VK_DIRECT_AUTH_*` — секреты: в `.env.example` только плейсхолдеры, в логах никогда не фигурируют.

---

## 3. Схема Telegram-диалога (FSM)

Механизм — существующие wait-states ([`set_wait_state()`](../tg/helpers.py:25),
[`reset_wait_state()`](../tg/helpers.py:19)); новый ContentTypes не нужен, всё текстовое.

### 3.1 Состояния

Добавить в [`WAIT_STATE_KEYS`](../tg/keyboards.py:31):

```python
WAIT_STATE_KEYS = (
    "await_vk_id", "await_vk_token", "await_tiktok_username", "await_tiktok_login",
    "await_vk_login",   # ждём «логин:пароль»
    "await_vk_code",    # ждём только код 2FA
    "await_vk_captcha", # ждём только текст капчи
)
```

Кнопка `BUTTON_VK_LOGIN` («VK вход (логин+пароль)») добавляется в [`MAIN_KEYBOARD`](../tg/keyboards.py:19)
рядом с [`BUTTON_TIKTOK_LOGIN`](../tg/keyboards.py:17).

### 3.2 Временный контекст диалога

Всё промежуточное состояние — в `context.user_data["vk_login_ctx"]` (dict), НЕ в БД:

| Ключ | Тип | Когда пишется | Назначение |
| --- | --- | --- | --- |
| `login` | str | старт диалога | повторный вызов auth с `code`/`captcha_key` |
| `password` | str | старт диалога | то же; никогда не логируется и не эхо |
| `validation_sid` | str | ветка `need_validation` | будущий re-auth/VK ID-флоу (v1 — только хранение) |
| `phone_mask` | str | ветка `need_validation` | показ пользователю, куда пришёл код |
| `captcha_sid` | str | ветка `need_captcha` | передача в повторный вызов |
| `attempts_code` | int | ветка `wrong_otp` | лимит повторов кода (см. §5) |
| `attempts_captcha` | int | ветка `need_captcha` | лимит повторов капчи (см. §5) |

Очистка: `_clear_vk_login_ctx(context)` = `context.user_data.pop("vk_login_ctx", None)` +
[`reset_wait_state()`](../tg/helpers.py:19). Вызывается при: успехе, финальных ошибках
(`bad_password`, `too_much_tries`, исчерпание повторов), команде `/cancel`, `/start`.

### 3.3 Маршрутизация ввода

В [`handle_text()`](../tg/handlers.py:188) после существующих wait-state веток (образец —
строки 234–248) добавить три ветки: `await_vk_login` → `vk_login_from_text`,
`await_vk_code` → `vk_code_from_text`, `await_vk_captcha` → `vk_captcha_from_text`.

- Если wait-state НЕ активен — срабатывает обычный роутинг (кнопки/число/@username/`unknown_input`).
  То есть «ошибка при повторном вводе» невозможна: неактивное состояние = обычная обработка текста.
- Кнопки главной клавиатуры обрабатываются ДО wait-веток (как сейчас), поэтому нажатие любой кнопки
  в середине диалога перезапускает другой флоу; `/start` и `/cancel` очищают контекст.

### 3.4 Повторный ввод и ошибки формата

- `await_vk_login`: невалидный формат → текст ошибки, состояние **сохраняется** (пользователь
  может прислать строку ещё раз). Формат принимается до успешного парса.
- `await_vk_code`: ввод свободный текст — trim и передача как есть (VK сам отклонит неверный код);
  непустой ввод обязателен (пустое сообщение ловится раньше — [`empty_text_message()`](../tg/messages.py:173)).
- `await_vk_captcha`: текст капчи передаётся как введён (без смены регистра).

---

## 4. Парсер ввода `логин:пароль`

Новая чистая функция в [`flows.py`](../tg/flows.py) — `_parse_vk_login_input(text)`,
по образцу [`_parse_login_input()`](../tg/flows.py:129), но БЕЗ требования `@`:

- разделитель — **первое** `:` (`split(":", 1)`): логин VK — телефон или email и не содержит `:`,
  пароль может содержать `:`;
- обе части `strip()`;
- валидно: логин непустой И пароль непустой (пустые после strip → `None`);
- начальные и конечные пробелы всей строки срезаются до парса.

Отличия от TikTok-парсера (важно для тестов): `@` не требуется (`+79001234567:pass` — валидно);
регистр сохраняется.

---

## 5. Поток вызова и маппинг статусов

### 5.1 Вызов

[`vk_direct_auth()`](../vk/auth.py:27) — **уже `async def`** (requests внутри
`run_in_executor`, строки 80–85). Обёртка `asyncio.to_thread`/`run_in_executor` **не нужна** —
вызываем `await vk_direct_auth(login, password)` напрямую. Внешний контроль — по аналогии с
TikTok-флоу: таймаут не требуется (таймаут транспортного запроса уже в
[`VK_API_TIMEOUT_SECONDS`](../config.py:125)), но общий `asyncio.wait_for` с запасом
(например, `VK_API_TIMEOUT_SECONDS + 30`) защищает от «залипания» executor-пула.

### 5.2 Дерево исходов

| Статус [`vk_direct_auth()`](../vk/auth.py:50) | Действие флоу | Состояние после |
| --- | --- | --- |
| `ok` (`access_token`, `user_id`) | 1) при `VK_STORE_PASSWORD=1` — [`save_vk_user_password()`](../db/vk_tokens.py:40); 2) [`save_vk_user_token()`](../db/vk_tokens.py:28); 3) post-check stories (§6); 4) `_clear_vk_login_ctx` | нет (главное меню) |
| `need_validation` | показать `phone_mask` (+`validation_type`), записать `validation_sid` в ctx, попросить код | `await_vk_code` |
| `need_captcha` | отправить фото [`captcha_img`](../vk/auth.py:116) (`send_photo` по URL) + попросить символы; `captcha_sid` в ctx | `await_vk_captcha` |
| `bad_password` | текст «неверный логин/пароль», `_clear_vk_login_ctx` | нет |
| `wrong_otp` | текст «неверный код»; если `attempts_code < 3` — снова просить код (`attempts_code += 1`), иначе сброс с предложением начать заново | `await_vk_code` или нет |
| `too_much_tries` | текст «слишком много попыток, подождите ~15 минут», `_clear_vk_login_ctx` | нет |
| `network_error` | текст «сеть недоступна, повторите ввод»; состояние **сохраняется** (retry): в `await_vk_login` — прислать `логин:пароль` снова, в `await_vk_code`/`await_vk_captcha` — повторить ввод | без изменений |
| прочее/`unknown_error` | generic-текст внутренней ошибки, `_clear_vk_login_ctx` | нет |

### 5.3 Структура флоу-функций (по образцу TikTok-блока [`flows.py`](../tg/flows.py:123))

- `ask_vk_login(update, context)` — гейт-проверка, `set_wait_state(context, "await_vk_login")`, приглашение.
- `vk_login_from_text(update, context, text)` — парс (§4) → ctx (`login`, `password`) → `await vk_direct_auth(...)`
  → `_handle_vk_auth_result(...)`.
- `vk_code_from_text(update, context, text)` — `await vk_direct_auth(ctx.login, ctx.password, code=text)` → `_handle_vk_auth_result(...)`.
- `vk_captcha_from_text(update, context, text)` — `await vk_direct_auth(ctx.login, ctx.password, captcha_sid=ctx.captcha_sid, captcha_key=text)` → `_handle_vk_auth_result(...)`.
- `_handle_vk_auth_result(update, context, tg_id, result)` — единый маппинг статусов (таблица §5.2),
  аналог [`_handle_tiktok_login_result()`](../tg/flows.py:145).
- Все тексты — билдеры в [`messages.py`](../tg/messages.py), ничего секретного (пароль/токен/логин)
  в тексты и логи не попадает (правило [`vk/auth.py`](../vk/auth.py:9) распространяется на слой tg).

---

## 6. Post-check stories при `ok`

Требование: убедиться, что токен умеет читать сторис, но **не блокировать сохранение**.

- [`check_token_works_for_stories()`](../vk/client.py:77) НЕ подходит для per-user проверки:
  она берёт глобальный токен через [`get_any_active_vk_token()`](../db/vk_tokens.py:83) (строка 83),
  а новый per-user токен ещё не является «активным глобальным» детерминированно.
- Поэтому post-check — прямой вызов [`vk_call()`](../vk/client.py:53):
  `vk_call("stories.get", {"v": "5.131", "owner_id": user_id, "access_token": <новый токен>})`.
  `user_id` берётся из ok-ответа; если у пользователя задан его VK ID — можно проверить его.
- Исходы:
  - stories.get ok → текст успеха («✅ Вход выполнен, токен сохранён…»).
  - stories.get fail (например, права/приватность) → токен **всё равно сохранён**, текст с
    предупреждением: «токен сохранён, но чтение сторис недоступно: <короткая причина>».
  - Транспортная ошибка post-check → трактовать как предупреждение, не как фейл входа.
- [`check_token_works_for_stories()`](../vk/client.py:77) остаётся глобальным инструментом
  (`/who`, token_watcher) — не меняется.

---

## 7. Схема БД

### 7.1 Изменений НЕТ — миграции не нужны

Таблица `vk_user_tokens` уже есть (миграции v1-эпохи), колонок достаточно:
`tg_id, token_enc, token_kind, login_enc, password_enc, created_at, updated_at`.
Используемое API: [`save_vk_user_token()`](../db/vk_tokens.py:28),
[`get_vk_user_token()`](../db/vk_tokens.py:52), [`save_vk_user_password()`](../db/vk_tokens.py:40),
[`get_vk_user_credentials()`](../db/vk_tokens.py:62), [`delete_vk_user_token()`](../db/vk_tokens.py:76),
[`get_any_active_vk_token()`](../db/vk_tokens.py:83). Шифрование внутри — `crypto.encrypt_str/decrypt_str`
(формат `enc:v1:<fernet>`).

### 7.2 Ветка default (`VK_STORE_PASSWORD=0`) — основная

1. `ok` → [`save_vk_user_token(tg_id, token)`](../db/vk_tokens.py:28) → строка `kind='token'`,
   `login_enc`/`password_enc` пустые.
2. Чтение: [`get_vk_user_token()`](../db/vk_tokens.py:52) возвращает расшифрованный токен.
3. Пароль после диалога нигде не остаётся (только RAM `vk_login_ctx`, очищается по §3.2).

### 7.3 Ветка flag (`VK_STORE_PASSWORD=1`) — опциональная

1. `ok` → [`save_vk_user_password(tg_id, login, password)`](../db/vk_tokens.py:40) → запись `kind='password'`.
2. Сразу же → [`save_vk_user_token(tg_id, token)`](../db/vk_tokens.py:28) → **REPLACE перезаписывает строку**
   (`INSERT OR REPLACE`, строки 33–37): итог `kind='token'`, пароль затёрт.
3. Осознанное следствие: одновременное хранение пароля и токена в v1 невозможно (одна строка на
   `tg_id`, `token_kind` взаимоисключает значения). Полноценное dual-хранение = будущая миграция
   (отдельная таблица `vk_user_passwords` или допколонки), не в скоупе.
4. Edge-ценность флага: если сохранение токена падает с исключением БД, пароль остаётся в записи
   `kind='password'` (данные для будущего re-auth и диагностики; не логируются).

### 7.4 Совместимость с глобальным мониторингом

- [`get_any_active_vk_token()`](../db/vk_tokens.py:83): settings override → `config.VK_TOKEN` →
  **первый попавшийся** user-токен (`ORDER BY tg_id LIMIT 1`, только `kind='token'`).
- После VK-входа per-user токены становятся fallback-кандидатами мониторинга — задокументированное
  поведение v1, [`get_vk_stories()`](../vk/stories.py:23) и `token_watcher` не меняются.
- Риск выбора «не того» токена для глобального мониторинга см. §9.

---

## 8. Тест-план (TDD-якоря)

Новый файл `tests/test_vk_login_flow.py`. Инфраструктура готова: [`db_isolated`](../tests/conftest.py:57)
(tmp-БД, миграции), autouse [`no_network`](../tests/conftest.py:89), паттерн мока
`vk.auth.requests.post` + `FakeResponse` — как в `tests/test_vk_auth.py`; контракт «TestNoSecretsInResult».

### 8.1 Парсер (`_parse_vk_login_input`)

- `test_parse_vk_login_ok_email_password`
- `test_parse_vk_login_ok_phone_password` (без `@` — отличие от TikTok)
- `test_parse_vk_login_strips_whitespace` (пробелы вокруг строки и вокруг частей)
- `test_parse_vk_login_colon_in_password` (`split(":", 1)`)
- `test_parse_vk_login_no_colon_returns_none`
- `test_parse_vk_login_empty_login_returns_none` (`:pass`, ` :pass`)
- `test_parse_vk_login_empty_password_returns_none` (`user:`)
- `test_parse_vk_login_whitespace_only_password_returns_none`

### 8.2 Гейт

- `test_vk_gate_disabled_without_client_credentials` (пустые env → disabled-текст, wait-state не ставится)
- `test_vk_gate_enabled_with_credentials`
- `test_vk_gate_force_disabled_by_env_flag` (`VK_LOGIN_ENABLED=0` при заполненных кредах)

### 8.3 Маппинг статусов (мок `vk_direct_auth` на уровне `flows`)

- `test_flow_ok_saves_token_per_user` ([`get_vk_user_token()`](../db/vk_tokens.py:52) в tmp-БД)
- `test_flow_ok_clears_ctx_and_wait_state`
- `test_flow_ok_store_password_flag_calls_save_vk_user_password` (spy на db-функцию)
- `test_flow_need_validation_sets_code_state_with_phone_mask`
- `test_flow_need_captcha_sends_photo_and_sets_captcha_state` (мок `bot.send_photo`)
- `test_flow_bad_password_clears_state`
- `test_flow_wrong_otp_allows_retry_up_to_limit`
- `test_flow_wrong_otp_resets_after_limit`
- `test_flow_too_much_tries_clears_state`
- `test_flow_network_error_keeps_state_for_retry` (все три состояния)
- `test_flow_unknown_error_clears_state`

### 8.4 Секреты и контекст

- `test_no_secrets_in_any_vk_login_text` — `repr` всех новых билдеров
  [`messages.py`](../tg/messages.py) не содержит пароль/логин/токен (паттерн TestNoSecretsInResult).
- `test_no_secrets_in_vk_flow_logs` (caplog: только маска телефона/типы ошибок).
- `test_code_context_persists_between_messages` (`user_data["vk_login_ctx"]` живёт между апдейтами).
- `test_captcha_context_persists_between_messages`
- `test_cancel_clears_vk_login_ctx`
- `test_post_check_failure_still_saves_token` (stories.get fail → токен в БД + warning-текст)
- `test_wait_state_keys_include_vk_states` (3 новых ключа в [`WAIT_STATE_KEYS`](../tg/keyboards.py:31))

Критерий готовности: все тесты зелёные, `pytest` без сети, секрет-скан репозитория чистый.

---

## 9. Риски и каскады

| # | Риск | Митигация в дизайне |
| --- | --- | --- |
| 1 | `too_much_tries` — rate-limit VK на прямую авторизацию (окно ~15 минут) | Отдельный текст с просьбой подождать; состояние сбрасывается; повторные попытки не автоматизируются. |
| 2 | Невалидный/истёкший `captcha_sid` при повторе: VK ответит новой `need_captcha` | Счётчик `attempts_captcha` (лимит 2): каждая новая `need_captcha` обновляет `captcha_sid` и шлёт свежую картинку; после лимита — сброс и предложение начать заново. |
| 3 | Капча-картинка по URL уже погашена/истекла к моменту показа | Текст подсказывает «если картинка не читается — отправьте любой текст, пришлём новую капчу» (повтор `need_captcha` даст новый `captcha_sid`). |
| 4 | Токен без прав на stories (scope/приватность) | Post-check §6: warning, токен сохраняется; вход не откатывается. |
| 5 | `wrong_otp` цикл: пользователь много раз шлёт неверный код | Лимит `attempts_code` = 3, затем сброс (иначе VK сам уйдёт в `too_much_tries`). |
| 6 | Несколько пользователей: [`get_any_active_vk_token()`](../db/vk_tokens.py:83) возьмёт `MIN(tg_id)` токен для глобального мониторинга | Зафиксировано в доке как известное поведение; мониторинг не меняем; при необходимости — отдельная задача «per-user мониторинг». |
| 7 | Пароль в RAM `user_data` переживает рестарт процесса? Нет: PTB persistence не включён — словари умирают с процессом | Приемлемо: диалог короткий; после рестарта пользователь вводит заново. |
| 8 | Утечка секрета через логи/тексты | Правила [`vk/auth.py`](../vk/auth.py:9) + тест-якоря §8.4; в логах только коды ошибок и маска телефона. |
| 9 | Замещение парольной записи токеновой (REPLACE) при `VK_STORE_PASSWORD=1` может удивить | Явно задокументировано в §7.3; рекомендуемое значение флага — `0`. |

---

## 10. Открытые вопросы (не блокируют v1)

1. Нужна ли отмена диалога кнопкой (reply-кнопка «Отмена») или достаточно `/cancel` + `/start`?
2. Лимиты `attempts_code`/`attempts_captcha` — константы в [`flows.py`](../tg/flows.py) (выбрано)
   или env-переменные?
3. Post-check: проверять на `user_id` из ok-ответа или на VK ID пользователя, если задан?
4. Re-auth по сохранённому паролю (при `VK_STORE_PASSWORD=1`): потребует миграции v8
   (отдельное хранение пароля) и отдельного дизайна.
5. Нужен ли per-user fallback в [`get_vk_stories()`](../vk/stories.py:23) (`get_vk_user_token(tg_id)`
   → потом глобальный) — кандидат в v2 вместе с per-user мониторингом.
6. Живая проверка 2FA/капчи на реальных аккаунтах (пункт из [activeContext](../../memory-bank/activeContext.md)).

---

## 11. Чеклист реализации по файлам

- [ ] `config.py`: `VK_LOGIN_ENABLED` (гейт из §2), `VK_STORE_PASSWORD` (default `0`); значения env
      не логируются.
- [ ] `tg/keyboards.py`: `BUTTON_VK_LOGIN`, вставка в [`MAIN_KEYBOARD`](../tg/keyboards.py:19),
      расширение [`WAIT_STATE_KEYS`](../tg/keyboards.py:31) тремя ключами.
- [ ] `tg/messages.py`: ~12 билдеров — приглашение ввода `логин:пароль`, bad_format, disabled
      («вход временно недоступен»), need_code (с `phone_mask`), bad_code, need_captcha (подсказка),
      ok, ok_with_warning (post-check), bad_password, too_much_tries, network_retry, generic_error.
- [ ] `tg/flows.py`: `_parse_vk_login_input`, `_clear_vk_login_ctx`, `ask_vk_login`,
      `vk_login_from_text`, `vk_code_from_text`, `vk_captcha_from_text`, `_handle_vk_auth_result`,
      `_post_check_stories` (прямой [`vk_call()`](../vk/client.py:53)).
- [ ] `tg/handlers.py`: импорт нового билдера кнопки, ветка кнопки в
      [`handle_text()`](../tg/handlers.py:188) до wait-блока, три wait-ветки после строк 234–248,
      очистка `vk_login_ctx` в `/cancel` (+ решение по `/start`).
- [ ] `tests/test_vk_login_flow.py`: полный набор из §8.
- [ ] `.env.example`: `VK_LOGIN_ENABLED`, `VK_STORE_PASSWORD`, комментарии к `VK_DIRECT_AUTH_*`
      (только плейсхолдеры, без реальных значений).
- [ ] НЕ менять: `vk/auth.py`, `db/vk_tokens.py`, `db/migrations.py`, `vk/stories.py`,
      `vk/client.py`.
- [ ] Финальные проверки: `pytest` зелёный; все файлы ≤500 строк; секрет-скан staged-диффа чистый.
