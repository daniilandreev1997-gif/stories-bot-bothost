"""Процесс-наблюдатель для app.py с контролем heartbeat."""
import os
import subprocess
import sys
import time
from pathlib import Path

RESTART_DELAY = 30
WATCHDOG_STEP_SECONDS = 5
PYTHON_EXE = sys.executable
BASE_DIR = Path(__file__).resolve().parent
HEARTBEAT_PATH = BASE_DIR / "bot.heartbeat"


def _stale_heartbeat() -> bool:
    """Вызывает общую утилиту, не мешая runner стартовать без config/.env."""
    try:
        from utils import is_heartbeat_stale

        try:
            stale_seconds = int(os.getenv("RUNNER_HEARTBEAT_STALE_SECONDS", "300"))
        except ValueError:
            stale_seconds = 300
        return is_heartbeat_stale(HEARTBEAT_PATH, stale_seconds)
    except (ImportError, ValueError):
        try:
            mtime = HEARTBEAT_PATH.stat().st_mtime
        except OSError:
            return True
        return time.time() - mtime >= 300


def _stop_process(process: subprocess.Popen) -> None:
    """Завершает процесс мягко, затем принудительно при зависании."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _watchdog(process: subprocess.Popen) -> int | None:
    """Ждёт завершения процесса или останавливает его при stale heartbeat."""
    while process.poll() is None:
        if _stale_heartbeat():
            print("[RUNNER] heartbeat stale -> terminate", flush=True)
            _stop_process(process)
            return process.returncode
        time.sleep(WATCHDOG_STEP_SECONDS)
    return process.returncode


def main() -> None:
    while True:
        process = None
        try:
            print("[RUNNER] starting app.py ...", flush=True)
            process = subprocess.Popen([PYTHON_EXE, "app.py"])
            code = _watchdog(process)
            print(f"[RUNNER] bot exited with code {code}", flush=True)
            if code == 0:
                return
        except KeyboardInterrupt:
            print("[RUNNER] stopped by user", flush=True)
            if process is not None:
                _stop_process(process)
            raise SystemExit(0)
        except Exception as exc:
            print(f"[RUNNER] start error: {exc!r}", flush=True)

        print(f"[RUNNER] restarting in {RESTART_DELAY} sec...", flush=True)
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
