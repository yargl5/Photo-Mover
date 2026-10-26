"""Windows Portable Devices bridge; JSON protocol, no shell command interpolation."""
import json
import subprocess
import sys
import queue
import threading
import time
from pathlib import Path


class TransferCancelled(Exception):
    pass


class Session:
    """One helper and one WPD connection per transfer batch, not per photo."""
    def __init__(self):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        self.process = subprocess.Popen(
            [str(base / "vendor" / "MediaDevices" / "MtpBridge.exe")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.answers = queue.Queue()
        def read():
            try:
                for line in self.process.stdout:
                    self.answers.put(line)
            finally:
                self.answers.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def request(self, command, cancelled=lambda: False, timeout=60, **values):
        if self.process.poll() is not None:
            raise OSError("Соединение MTP закрыто. Оригинал сохранён.")
        self.process.stdin.write(json.dumps(dict(command=command, **values), ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        started = time.monotonic()
        while True:
            if cancelled():
                self.close()
                raise TransferCancelled("Перенос остановлен. Оригинал сохранён.")
            if time.monotonic() - started > timeout:
                self.close()
                raise TimeoutError("Телефон не ответил вовремя. Оригинал сохранён.")
            try:
                line = self.answers.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                raise OSError("Соединение MTP прервано. Оригинал сохранён.")
            result = json.loads(line.lstrip("\ufeff"))
            if not result.get("ok"):
                raise OSError(result.get("error", "Ошибка MTP"))
            return result

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdout.close()

    def __enter__(self): return self

    def __exit__(self, *_): self.close()


def request(command, cancelled=lambda: False, timeout=60, **values):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    helper = base / "vendor" / "MediaDevices" / "MtpBridge.exe"
    process = subprocess.Popen(
        [str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    import time
    started = time.monotonic()
    payload = json.dumps(dict(command=command, **values), ensure_ascii=False)
    try:
        while True:
            if cancelled():
                raise TransferCancelled("Перенос остановлен. Оригинал сохранён.")
            if time.monotonic() - started > timeout:
                raise TimeoutError("Телефон не ответил вовремя. Оригинал сохранён.")
            try:
                stdout, stderr = process.communicate(payload, timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                payload = None
        try:
            result = json.loads(stdout.lstrip("\ufeff"))
        except ValueError as exc:
            raise OSError("MTP-модуль не ответил: " + (stderr or stdout)[-500:]) from exc
        if process.returncode or not result.get("ok"):
            raise OSError(result.get("error", "Ошибка MTP"))
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
