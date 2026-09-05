"""Тексты сообщений Telegram-бота — константы и функции-строители.

Все тексты перенесены ДОСЛОВНО из bot_host.py (подзадача A2): f-строки
из обработчиков/хелперов вынесены сюда без изменения ни одного символа,
чтобы поведение бота не изменилось.
"""
from .keyboards import (
    BUTTON_CHECK_NOW,
    BUTTON_CLEAR_TOKEN,
    BUTTON_LIST,
    BUTTON_SILENT,
    BUTTON_TIKTOK,
    BUTTON_TIKTOK_RESET,
    BUTTON_TOKEN_VK,
    BUTTON_VK_ID,
)


def help_text() -> str:
    """Текст /start (дословно из start_cmd монолита)."""
    return (
        "Бот присылает сторис VK и новые посты TikTok в этот чат.\n\n"
        "С чего начать: нажмите VK ID или TikTok @username и пришлите значение.\n\n"
        "Кнопки:\n"
        "• VK ID - привязать VK-аккаунт (пришлите число)\n"
        "• TikTok @username - привязать TikTok-аккаунт\n"
        "• Token VK - сохранить ваш VK-токен (нужен, если серверный не настроен)\n"
        "• Сброс VK token - удалить ваш токен и вернуться к серверному\n"
        "• Проверить сейчас - проверка подписок, не дожидаясь цикла\n"
        "• Кого мониторю - список подписок и статус токена\n"
        "• Сброс TikTok истории - прислать последние посты заново\n"
        "• Тихий режим - вкл/выкл технические уведомления\n"
        "• TikTok вход (логин+пароль) - вход в TikTok, если посты не приходят без него\n\n"
        "Слэш-команды тоже работают: /checknow /list /silent /who /token /tiktok /tiktokreset /cleartoken"
    )


def ask_vk_id_text() -> str:
    """Приглашение ввода VK ID (ask_vk_id)."""
    return "Пришли VK ID (число)."


def ask_vk_token_text() -> str:
    """Приглашение ввода VK token (ask_vk_token)."""
    return "Пришли новый VK token следующим сообщением."


def ask_tiktok_username_text() -> str:
    """Приглашение ввода TikTok username (ask_tiktok_username)."""
    return "Пришлите TikTok username в формате @username или ссылкой на профиль, например tiktok.com/@iozb8"


def empty_token_text() -> str:
    """Пустой токен (set_vk_token_from_text)."""
    return "Пустой токен. Пришли непустое значение."


def bad_vk_id_text() -> str:
    """VK ID не число (set_vk_id_from_text)."""
    return "VK ID должен быть числом."


def vk_id_saved_text(vk_id: str) -> str:
    """VK ID сохранён (set_vk_id_from_text)."""
    return f"✅ Мониторю VK ID: {vk_id}"


def bad_tiktok_username_text() -> str:
    """TikTok username не распознан (set_tiktok_username_from_text)."""
    return "Не смог распознать TikTok username. Пример: @iozb8"


def tiktok_username_saved_text(username: str) -> str:
    """TikTok username сохранён (set_tiktok_username_from_text)."""
    return f"✅ Мониторю TikTok @{username}. Запускаю первую синхронизацию (загружу доступные посты)."


def silent_status_text(status: str) -> str:
    """Статус тихого режима (silent_cmd): status = 'ВКЛЮЧЕНА (...)' | 'ВЫКЛЮЧЕНА (...)')."""
    return f"🤫 Тишина {status}"


def silent_on_status() -> str:
    """Статус при включении тихого режима (дословная строка silent_cmd)."""
    return "ВКЛЮЧЕНА (тех. уведомлений не будет)"


def silent_off_status() -> str:
    """Статус при выключении тихого режима (дословная строка silent_cmd)."""
    return "ВЫКЛЮЧЕНА (уведомления будут приходить)"


def list_lines(vk_id: str | None, tiktok_username: str | None, token_state: str) -> list[str]:
    """Строки списка мониторинга (list_cmd): vk_id/username/состояние токена.

    token_state — 'плохой' либо 'ok' (как в монолите).
    """
    lines = []
    if vk_id:
        lines.append(f"• VK ID: {vk_id}")
    else:
        lines.append("• VK ID: не задан")

    if tiktok_username:
        lines.append(f"• TikTok: @{tiktok_username}")
    else:
        lines.append("• TikTok: не задан")

    lines.append(f"• VK token status: {token_state}")
    return lines


def list_header() -> str:
    """Заголовок списка мониторинга (list_cmd)."""
    return "Кого мониторим:\n"


def no_vk_id_text() -> str:
    """VK ID не задан (who_cmd, checknow_cmd-ветка VK не используется)."""
    return "Сначала укажи VK ID."


def token_ok_text() -> str:
    """Токен работает (who_cmd, token_watcher)."""
    return "✅ VK токен работает."


def token_problem_text(reason: str) -> str:
    """Проблема с токеном (who_cmd)."""
    return f"❌ Проблема с VK токеном: {reason}"


def no_targets_text() -> str:
    """Нечего проверять (checknow_cmd)."""
    return "Сначала укажи VK ID или TikTok username."


def checking_vk_text() -> str:
    """Статус проверки VK (checknow_cmd)."""
    return "Проверяю VK..."


def checking_tiktok_text(username: str) -> str:
    """Статус проверки TikTok (checknow_cmd)."""
    return f"Проверяю TikTok @{username}..."


def done_text() -> str:
    """Проверка завершена (checknow_cmd)."""
    return "Готово."


def token_saved_text() -> str:
    """Токен сохранён (set_vk_token_from_text)."""
    return "✅ VK token обновлен."


def token_cleared_text() -> str:
    """Токен сброшен (cleartoken_cmd)."""
    return "✅ VK token override сброшен. Используется fallback токен."


def tiktok_reset_text(username: str) -> str:
    """История TikTok сброшена (tiktokreset_cmd)."""
    return f"✅ Сбросил историю TikTok для @{username}. Запускаю повторную загрузку."


def no_tiktok_username_text() -> str:
    """TikTok username не задан (tiktokreset_cmd)."""
    return "Сначала укажи TikTok username."


def empty_text_message() -> str:
    """Пустое сообщение (handle_text)."""
    return "Пустое сообщение. Нажмите кнопку или пришлите значение."


def unknown_input_text() -> str:
    """Непонятный ввод (handle_text, финальная ветка)."""
    return (
        "Не понял это сообщение.\n"
        "Бот принимает:\n"
        "• число - сохранит как VK ID\n"
        "• @username или ссылку на профиль TikTok\n"
        "• VK-токен - если до этого нажали Token VK\n"
        "Для остального используйте кнопки ниже."
    )


def ask_tiktok_login_text() -> str:
    """Приглашение ввода TikTok login (ask_tiktok_login)."""
    return (
        "Пришли TikTok логин одной строкой в формате:\n"
        "email:password\n\n"
        "Бот выполнит вход и сохранит cookies шифрованно. "
        "Пароль и cookies никогда не показываются и не логируются."
    )


def tiktok_login_ok_text(masked_email: str) -> str:
    """Успешный TikTok-вход (set_tiktok_login_from_text); email маскирован."""
    return (
        f"✅ Вход в TikTok выполнен ({masked_email}), cookies сохранены. "
        "Следующий цикл мониторинга использует их автоматически."
    )


def tiktok_login_captcha_text() -> str:
    """TikTok показал капчу (set_tiktok_login_from_text)."""
    return (
        "🤖 TikTok показал капчу, автоматический вход не удался.\n"
        "Попробуйте позже ещё раз. Если капча повторяется - попросите администратора настроить ручные cookies (TIKTOK_COOKIES_FILE на сервере), мониторинг заработает и без входа."
    )


def tiktok_login_bad_credentials_text() -> str:
    """Неверный логин/пароль TikTok (set_tiktok_login_from_text)."""
    return "❌ TikTok: неверный логин или пароль. Проверьте данные и повторите."


def tiktok_login_unavailable_text() -> str:
    """playwright не установлен (set_tiktok_login_from_text)."""
    return (
        "⚙️ Модуль playwright не установлен на сервере — используйте ручные cookies "
        "(TIKTOK_COOKIES_FILE)."
    )


def tiktok_login_timeout_text() -> str:
    """Таймаут TikTok-входа (set_tiktok_login_from_text)."""
    return "⏱ Превышено время входа, попробуйте позже."


def tiktok_login_disabled_text() -> str:
    """Вход по логину/паролю отключён (set_tiktok_login_from_text)."""
    return (
        "🔒 Вход по логину/паролю отключён (TIKTOK_LOGIN_ENABLED=0); "
        "используйте ручные cookies."
    )


def tiktok_login_bad_format_text() -> str:
    """Неверный формат ввода login:password (set_tiktok_login_from_text)."""
    return (
        "Формат не распознан. Пришлите одну строку:\n"
        "email:password\n"
        "email - до первого двоеточия, пароль - после (в пароле двоеточие допустимо)."
    )


def tiktok_login_error_text() -> str:
    """Внутренняя ошибка входа TikTok (set_tiktok_login_from_text)."""
    return "⚠️ Внутренняя ошибка входа. Попробуйте позже."


def token_recovered_text() -> str:
    """Токен снова работает (token_watcher, рассылка всем пользователям)."""
    return "✅ VK токен снова работает."


def token_broken_text(reason: str) -> str:
    """Токен не работает (token_watcher, рассылка всем пользователям)."""
    return (
        "❌ VK токен не работает.\n"
        f"{reason}\n\n"
        "Проверка VK сторис остановлена. Пришли новый /token"
    )


# =======================
# VK вход по логину/паролю (vk_login_flows)
# =======================
# ПРАВИЛО СЕКРЕТОВ: ни один билдер ниже не принимает и не включает логин,
# пароль, токен или код 2FA. В текстах — только маска телефона (phone_mask,
# отдаётся самим VK) и причины ошибок без секретов.

def vk_login_intro_text() -> str:
    """Приглашение ввода VK логина (ask_vk_login)."""
    return (
        "Пришли VK логин одной строкой в формате:\n"
        "login:password\n\n"
        "login - телефон или email VK; пароль - после первого двоеточия "
        "(двоеточие внутри пароля допустимо).\n"
        "Бот выполнит вход и сохранит токен шифрованно. "
        "Пароль и токен никогда не показываются и не логируются."
    )


def vk_login_bad_format_text() -> str:
    """Неверный формат ввода login:password (vk_login_from_text)."""
    return (
        "Формат не распознан. Пришлите одну строку:\n"
        "login:password\n"
        "login - до первого двоеточия, пароль - после (в пароле двоеточие допустимо)."
    )


def vk_login_disabled_text() -> str:
    """VK-вход по логину/паролю отключён на сервере (ask_vk_login)."""
    return "🔒 Вход по VK логину/паролю временно недоступен. Используйте кнопку Token VK."


def vk_need_code_text(phone_mask: str) -> str:
    """Требуется код 2FA (ветка need_validation); phone_mask отдаёт VK."""
    shown = phone_mask.strip() or "неизвестный номер"
    return (
        f"🔐 VK отправил код подтверждения на {shown}.\n"
        "Пришли код следующим сообщением."
    )


def vk_need_captcha_text() -> str:
    """Требуется капча (ветка need_captcha); картинка отправляется отдельно."""
    return (
        "🤖 VK показал капчу - на картинке выше.\n"
        "Пришли текст с картинки следующим сообщением.\n"
        "Если картинка не читается - отправь любой текст, пришлём новую капчу."
    )


def vk_login_ok_text() -> str:
    """Успешный VK-вход (ветка ok): без токена и пароля в тексте."""
    return "✅ Вход в VK выполнен, токен сохранён шифрованно. Мониторинг сторис будет использовать его автоматически."


def vk_login_ok_with_warning_text(reason: str) -> str:
    """Успешный вход, но post-check stories не прошёл (warning, не фейл)."""
    return (
        "✅ Вход в VK выполнен, токен сохранён.\n"
        f"⚠️ Чтение сторис этим токеном недоступно: {reason}"
    )


def vk_bad_password_text() -> str:
    """Неверный логин/пароль VK (ветка bad_password)."""
    return "❌ VK: неверный логин или пароль. Попробуй войти заново."


def vk_wrong_otp_text(remaining: int) -> str:
    """Неверный код 2FA (ветка wrong_otp); remaining - сколько попыток осталось."""
    return f"❌ Неверный код. Осталось попыток: {remaining}"


def vk_too_much_tries_text() -> str:
    """Слишком много попыток входа (ветка too_much_tries)."""
    return "🚫 VK: слишком много попыток входа. Подожди примерно 15 минут и начни заново."


def vk_network_error_retry_text() -> str:
    """Сетевая ошибка при входе (ветка network_error): состояние сохранено."""
    return "📡 Сеть недоступна при входе в VK. Попробуй ещё раз: пришли данные повторно или /start для отмены."


def vk_login_error_text() -> str:
    """Внутренняя/неизвестная ошибка входа VK (прочие статусы)."""
    return "⚠️ Внутренняя ошибка входа в VK. Попробуйте позже."
