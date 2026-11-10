"""Проверка завершения сервера после закрытия вкладки браузера."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="tasker-lifecycle-test-"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def api(port: int, method: str, path: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, response.read()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def main() -> int:
    port = free_port()
    env = os.environ.copy()
    env["TASKER_CONFIG_DIR"] = str(TMP / "config")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("TASKER_DB", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--no-browser",
            "--auto-shutdown",
            "--port",
            str(port),
            "--db",
            str(TMP / "tasker.db"),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        for _ in range(80):
            try:
                status, _ = api(port, "GET", "/api/info")
                if status == 200:
                    break
            except Exception:
                pass
            if process.poll() is not None:
                out, err = process.communicate()
                raise RuntimeError(f"Сервер не запустился:\n{out}\n{err}")
            time.sleep(0.15)
        else:
            raise RuntimeError("Сервер не ответил")

        status, _ = api(
            port, "POST", "/api/session/open", {"session_id": "tab-one"}
        )
        check(status == 200, "вкладка регистрируется")
        time.sleep(1.5)
        check(process.poll() is None, "пока вкладка открыта, сервер работает")

        status, _ = api(port, "POST", "/api/session/close?session_id=tab-one")
        check(status == 200, "закрытие вкладки принимается")
        time.sleep(1.0)
        status, _ = api(
            port, "POST", "/api/session/open", {"session_id": "tab-after-reload"}
        )
        check(status == 200, "новая вкладка успевает открыться")
        time.sleep(4.0)
        check(process.poll() is None, "перезагрузка не выключает сервер")

        status, _ = api(
            port, "POST", "/api/session/close?session_id=tab-after-reload"
        )
        check(status == 200, "последняя вкладка закрывается")
        code = process.wait(timeout=8)
        check(code == 0, "сервер завершился после закрытия последней вкладки")
        check((TMP / "tasker.db").is_file(), "файл базы остался на диске")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    print("Все проверки завершения по вкладке пройдены")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except AssertionError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        print(f"Временные файлы сохранены: {TMP}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(TMP, ignore_errors=True)
        raise SystemExit(code)
