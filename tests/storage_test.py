"""Проверка смены файла базы, сохранения пути и статуса записи."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="tasker-storage-test-"))
CONFIG = TMP / "config"
CONFIG.mkdir()
A = TMP / "a" / "tasker.db"
B = TMP / "b" / "second.db"
BAD = TMP / "bad.db"
FOREIGN = TMP / "foreign.db"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def api(port: int, method: str, path: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def start(port: int, extra: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["TASKER_CONFIG_DIR"] = str(CONFIG)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("TASKER_DB", None)
    process = subprocess.Popen(
        [sys.executable, "server.py", "--no-browser", "--port", str(port), *extra],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(80):
        try:
            status, _ = api(port, "GET", "/api/info")
            if status == 200:
                return process
        except Exception:
            pass
        if process.poll() is not None:
            out, err = process.communicate()
            raise RuntimeError(f"Сервер не запустился:\n{out}\n{err}")
        time.sleep(0.15)
    process.kill()
    out, err = process.communicate()
    raise RuntimeError(f"Сервер не ответил:\n{out}\n{err}")


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def main() -> int:
    print("Проверка пути к базе и статуса сохранения")
    port = free_port()
    process = start(port, ["--db", str(A)])
    try:
        status, info = api(port, "GET", "/api/info")
        check(status == 200, "GET /api/info отвечает 200")
        db = info["db"]
        check(db["path"] == str(A), "стартовый путь совпадает с --db")
        check(db["exists"] and db["writable"], "файл существует и доступен для записи")
        check(db["open"] and db["saved"], "база открыта, изменения сохраняются")
        check(db["revision"] == 0, "до записей revision равен 0")
        check(str(CONFIG) in db["config_path"], "config_path указывает на настройки")
        check(db["default_path"] != str(A), "путь по умолчанию отличается от --db")
        check(
            info["preferences"]
            == {
                "show_project_name": True,
                "show_completed": True,
                "sort_projects_by_tasks": False,
                "show_weekends": True,
            },
            "настройки календаря по умолчанию включены",
        )

        status, error = api(
            port,
            "POST",
            "/api/settings/calendar",
            {"show_completed": "no"},
        )
        check(status == 400, "небулево значение настройки отклоняется")
        status, saved = api(
            port,
            "POST",
            "/api/settings/calendar",
            {
                "show_project_name": False,
                "show_completed": False,
                "sort_projects_by_tasks": True,
                "show_weekends": False,
            },
        )
        check(status == 200, "настройки календаря сохраняются")
        check(
            saved["preferences"]
            == {
                "show_project_name": False,
                "show_completed": False,
                "sort_projects_by_tasks": True,
                "show_weekends": False,
            },
            "ответ содержит новые настройки",
        )
        stored = json.loads((CONFIG / "settings.json").read_text(encoding="utf-8"))
        check(
            stored.get("calendar")
            == {
                "show_project_name": False,
                "show_completed": False,
                "sort_projects_by_tasks": True,
                "show_weekends": False,
            },
            "настройки календаря записаны в settings.json",
        )

        status, created = api(port, "POST", "/api/projects", {"name": "Проект A"})
        check(status == 201, "проект в базе A создаётся")
        project_a = created["project"]
        status, info = api(port, "GET", "/api/info")
        check(info["db"]["revision"] > 0, "revision растёт после записи")
        check(bool(info["db"]["last_write_at"]), "время последней записи заполнено")

        status, switched = api(port, "POST", "/api/db/switch", {"path": str(B)})
        check(status == 200, "переключение на новый файл B работает")
        check(switched["path"] == str(B), "ответ содержит новый путь")
        check(B.is_file(), "новый файл базы создан")
        check(switched["info"]["stats"]["projects"] == 0, "новая база пустая")
        check(
            switched["info"]["preferences"]
            == {
                "show_project_name": False,
                "show_completed": False,
                "sort_projects_by_tasks": True,
                "show_weekends": False,
            },
            "настройки календаря сохраняются при смене базы",
        )
        stored = json.loads((CONFIG / "settings.json").read_text(encoding="utf-8"))
        check(stored.get("db_path") == str(B), "путь B сохранён в настройках")

        status, created = api(port, "POST", "/api/projects", {"name": "Проект B"})
        check(status == 201, "проект в базе B создаётся")
        project_b = created["project"]

        status, switched = api(port, "POST", "/api/db/switch", {"path": str(A)})
        check(status == 200, "возврат на базу A работает")
        check(switched["info"]["stats"]["projects"] == 1, "в базе A один проект")
        status, bootstrap = api(port, "GET", "/api/bootstrap")
        check(bootstrap["projects"][0]["id"] == project_a["id"], "вернулся проект A")

        BAD.write_text("not a sqlite database", encoding="utf-8")
        status, error = api(port, "POST", "/api/db/switch", {"path": str(BAD)})
        check(status == 400 and "SQLite" in error["error"], "не-SQLite файл отклоняется")
        status, info = api(port, "GET", "/api/info")
        check(info["db"]["path"] == str(A), "после ошибки остаётся база A")

        connection = sqlite3.connect(FOREIGN)
        connection.execute("CREATE TABLE other (id INTEGER)")
        connection.commit()
        connection.close()
        status, error = api(port, "POST", "/api/db/switch", {"path": str(FOREIGN)})
        check(status == 400 and "Tasker" in error["error"], "чужая SQLite-база отклоняется")
        status, info = api(port, "GET", "/api/info")
        check(info["db"]["path"] == str(A), "после второй ошибки остаётся база A")

        status, switched = api(port, "POST", "/api/db/switch", {"path": str(B)})
        check(status == 200, "переключение на B для проверки перезапуска")
    finally:
        stop(process)

    port = free_port()
    process = start(port, [])
    try:
        status, bootstrap = api(port, "GET", "/api/bootstrap")
        check(status == 200, "сервер запускается без --db")
        check(
            bootstrap["info"]["db"]["path"] == str(B),
            "после перезапуска используется сохранённый путь B",
        )
        check(
            bootstrap["info"]["preferences"]
            == {
                "show_project_name": False,
                "show_completed": False,
                "sort_projects_by_tasks": True,
                "show_weekends": False,
            },
            "после перезапуска восстанавливаются настройки календаря",
        )
        ids = [project["id"] for project in bootstrap["projects"]]
        check(project_b["id"] in ids, "в базе B на месте проект B")
        check(project_a["id"] not in ids, "проекта A в базе B нет")
    finally:
        stop(process)

    print("Все проверки пути к базе пройдены")
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
