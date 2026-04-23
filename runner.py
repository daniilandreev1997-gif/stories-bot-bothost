import subprocess
import sys
import time
import os

RESTART_DELAY = 30  # секунд
PYTHON_EXE = sys.executable  # текущий интерпретатор python

def main():
    while True:
        try:
            print("[RUNNER] starting bot_host.py ...", flush=True)

            # Запускаем бота как отдельный процесс
            p = subprocess.Popen([PYTHON_EXE, "bot_host.py"])

            code = p.wait()
            print(f"[RUNNER] bot exited with code {code}", flush=True)

        except KeyboardInterrupt:
            print("[RUNNER] stopped by user", flush=True)
            sys.exit(0)

        except Exception as e:
            print(f"[RUNNER] start error: {e!r}", flush=True)

        print(f"[RUNNER] restarting in {RESTART_DELAY} sec...", flush=True)
        time.sleep(RESTART_DELAY)

if __name__ == "__main__":
    main()
