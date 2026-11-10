#!/usr/bin/env python3
"""Smoke-тесты HTTP API Tasker. Зависимостей нет, запуск:

    python tests/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PORT = 8799
TMP_DIR = Path(tempfile.mkdtemp(prefix="tasker-test-"))
DB_PATH = TMP_DIR / "tasker.db"

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)

proc: subprocess.Popen | None = None
checks = 0
current_port = PORT


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def start_server(port: int) -> None:
    global proc, current_port
    current_port = port
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(BASE / "server.py"),
            "--port",
            str(port),
            "--db",
            str(DB_PATH),
            "--no-browser",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    url = f"http://127.0.0.1:{port}/api/bootstrap"
    for _ in range(150):
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"Сервер не запустился:\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError("Сервер не ответил вовремя")


def stop_server() -> None:
    global proc
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    proc = None


def call(method: str, path: str, body=None, headers: dict | None = None):
    data = None
    all_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{current_port}{path}",
        data=data,
        headers=all_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {"raw": raw}
        return exc.code, payload


def multipart_body(fields: dict[str, str], files: list[tuple[str, str, bytes]]):
    boundary = "----TaskerTest" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for filename, mime, content in files:
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode("utf-8")
        )
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def main() -> None:
    print(f"Временная база: {DB_PATH}")
    start_server(PORT)
    try:
        print("\n[1] Пустой старт и проекты")
        status, data = call("GET", "/api/bootstrap")
        check(status == 200, "GET /api/bootstrap отвечает 200")
        check(data["projects"] == [], "на старте нет проектов")
        check("db" in data["info"], "bootstrap отдаёт сведения о базе")

        status, data = call("POST", "/api/projects", {"name": "  Тестовый проект  "})
        check(status == 201, "проект создаётся")
        project = data["project"]
        project_id = project["id"]
        check(project["name"] == "Тестовый проект", "имя проекта очищается от пробелов")
        check(project["counts"] == {"urgent": 0, "later": 0, "done": 0}, "счётчики пустые")
        check(project["messages"] == 0, "заметок пока нет")

        status, _ = call("POST", "/api/projects", {"name": "   "})
        check(status == 400, "пустое имя проекта отклоняется")

        status, data = call("PATCH", f"/api/projects/{project_id}", {"name": "Рабочий проект"})
        check(status == 200 and data["project"]["name"] == "Рабочий проект", "проект переименовывается")

        print("\n[2] Задачи")
        tasks = {}
        for title, column in [
            ("Срочная задача", "urgent"),
            ("Позже", "later"),
            ("Готовое дело", "done"),
        ]:
            status, data = call(
                "POST",
                f"/api/projects/{project_id}/tasks",
                {"title": title, "description": f"Описание: {title}", "status": column},
            )
            check(status == 201, f"задача «{title}» создаётся")
            tasks[title] = data["task"]
        check(len(tasks) == 3, "три задачи созданы")

        status, _ = call(
            "POST", f"/api/projects/{project_id}/tasks", {"title": " ", "status": "urgent"}
        )
        check(status == 400, "пустая задача отклоняется")

        status, _ = call(
            "POST", f"/api/projects/{project_id}/tasks", {"title": "x", "status": "bad"}
        )
        check(status == 400, "неизвестная колонка отклоняется")

        status, data = call(
            "POST",
            f"/api/tasks/{tasks['Срочная задача']['id']}/move",
            {"status": "later", "index": 0},
        )
        check(status == 200, "задача перемещается на доске")
        check(data["task"]["status"] == "later", "новая колонка сохранена")
        check(data["project"]["counts"] == {"urgent": 0, "later": 2, "done": 1}, "счётчики пересчитаны")

        status, data = call("GET", f"/api/projects/{project_id}/tasks")
        by_status = {}
        for task in data["tasks"]:
            by_status.setdefault(task["status"], []).append(task)
        check(len(by_status["later"]) == 2, "в колонке «Потом» две задачи")
        check(
            by_status["later"][0]["title"] == "Срочная задача",
            "перемещённая задача встала на нулевую позицию",
        )

        status, data = call(
            "PATCH",
            f"/api/tasks/{tasks['Позже']['id']}",
            {"title": "Переименованная", "description": "Новое описание", "status": "done"},
        )
        check(status == 200 and data["task"]["title"] == "Переименованная", "задача редактируется")
        check(data["task"]["done_at"] is not None, "у выполненной задачи есть done_at")

        print("\n[3] Заметки и вложения")
        body, content_type = multipart_body(
            {"text": "Заметка с фотографией и файлом"},
            [
                ("скриншот.png", "image/png", PNG_1X1),
                ("договор.txt", "text/plain", "Текст договора".encode("utf-8")),
            ],
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{current_port}/api/projects/{project_id}/messages",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            data = json.loads(response.read().decode("utf-8"))
        check(status == 201, "заметка с файлами создаётся")
        message = data["message"]
        check(len(message["attachments"]) == 2, "оба вложения сохранены")
        image = next(a for a in message["attachments"] if a["is_image"])
        text_file = next(a for a in message["attachments"] if not a["is_image"])
        check(image["filename"] == "скриншот.png", "имя файла с кириллицей сохранено")
        check(text_file["size"] == len("Текст договора".encode("utf-8")), "размер файла верный")
        check(data["project"]["messages"] == 1, "счётчик заметок обновился")

        status, blob = call("GET", image["url"])
        check(status == 200 and blob == PNG_1X1, "картинка отдаётся байт в байт")

        request = urllib.request.Request(
            f"http://127.0.0.1:{current_port}{image['url']}?download=1"
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            disposition = response.headers.get("Content-Disposition", "")
            downloaded = response.read()
        check(downloaded == PNG_1X1, "скачивание работает")
        check(disposition.startswith("attachment;"), "скачивание отдаётся как attachment")

        status, data = call("PATCH", f"/api/messages/{message['id']}", {"text": "Обновлённая заметка"})
        check(status == 200 and data["message"]["body"] == "Обновлённая заметка", "заметка редактируется")

        status, _ = call("POST", f"/api/projects/{project_id}/messages", {"text": ""})
        check(status == 400, "пустая заметка без файлов отклоняется")

        print("\n[4] Пагинация и статистика")
        for index in range(4):
            call(
                "POST",
                f"/api/projects/{project_id}/messages",
                {"text": f"Заметка {index}"},
            )
        status, data = call("GET", f"/api/projects/{project_id}/messages?limit=2")
        check(status == 200 and len(data["messages"]) == 2, "limit ограничивает выдачу")
        check(data["has_more"] is True, "has_more выставляется")
        oldest = data["messages"][0]["created_at"]
        status, data2 = call(
            "GET", f"/api/projects/{project_id}/messages?limit=2&before={oldest}"
        )
        check(status == 200 and len(data2["messages"]) == 2, "пагинация по before работает")

        status, info = call("GET", "/api/info")
        check(status == 200, "GET /api/info работает")
        check(info["stats"]["projects"] == 1, "статистика видит проект")
        check(info["stats"]["attachments"] == 2, "статистика видит два вложения")
        check(info["stats"]["attachment_bytes"] == len(PNG_1X1) + len("Текст договора".encode("utf-8")), "размер вложений учитывается")
        check(info["db"]["size"] > 0, "размер файла БД известен")

        print("\n[5] Резервные копии")
        status, data = call("POST", "/api/backup")
        check(status == 201 and Path(data["path"]).is_file(), "резервная копия создаётся")
        backup_path = Path(data["path"])
        with sqlite3.connect(backup_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        check(count == 3, "в копии есть задачи")

        status, snapshot = call("GET", "/api/backup/download")
        check(status == 200 and snapshot[:16] == b"SQLite format 3\x00", "скачивается файл SQLite")
        snapshot_path = TMP_DIR / "snapshot.db"
        snapshot_path.write_bytes(snapshot)
        with sqlite3.connect(snapshot_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        check(count == 5, "в скачанной копии есть заметки")

        print("\n[6] Ошибки и каскадное удаление")
        status, _ = call("GET", "/api/tasks/does-not-exist")
        check(status == 404, "несуществующий маршрут даёт 404")
        status, _ = call("GET", "/api/attachments/deadbeef")
        check(status == 404, "несуществующий файл даёт 404")
        status, _ = call("GET", "/api/bootstrap", headers={"Host": "evil.example"})
        check(status == 403, "чужой Host отклоняется (защита от DNS rebinding)")
        status, _ = call(
            "POST",
            "/api/projects",
            {"name": "Из чужого источника"},
            headers={"Origin": "http://evil.example"},
        )
        check(status == 403, "запрос с чужого Origin отклоняется")

        status, data = call("DELETE", f"/api/messages/{message['id']}")
        check(status == 200 and data["project"]["messages"] == 4, "заметка удаляется")

        status, data = call("DELETE", f"/api/projects/{project_id}")
        check(status == 200 and data["ok"] is True, "проект удаляется")
        status, data = call("GET", "/api/bootstrap")
        check(data["projects"] == [], "проектов не осталось")

        status, data = call("POST", "/api/projects", {"name": "До перезапуска"})
        persistent_id = data["project"]["id"]

        print("\n[7] Перезапуск и сохранность файла")
        stop_server()
        check(DB_PATH.is_file(), "файл базы существует на диске")
        start_server(PORT + 1)
        status, data = call("GET", "/api/bootstrap")
        check(any(p["id"] == persistent_id for p in data["projects"]), "данные пережили перезапуск")

        print(f"\nВсе проверки пройдены: {checks}")
    finally:
        stop_server()


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nТЕСТ НЕ ПРОЙДЕН: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\nОШИБКА: {exc}", file=sys.stderr)
        raise
