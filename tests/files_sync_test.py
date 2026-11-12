#!/usr/bin/env python3
"""Проверки разделения базы и папки файлов, ZIP-копии и обмена с Диском."""

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
import uuid
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="tasker-files-test-"))
DB = TMP / "tasker.db"
DRIVE = TMP / "drive"
CONFIG = TMP / "config"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


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
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode()) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode()}") from exc


def multipart(text: str, files: list[tuple[str, str, bytes]], content_type: str) -> bytes:
    boundary = "----tasker" + uuid.uuid4().hex
    chunks = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="text"\r\n\r\n{text}\r\n'.encode()
    ]
    for filename, mime, content in files:
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
                f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode()
            + content
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def start(port: int):
    CONFIG.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TASKER_CONFIG_DIR=str(CONFIG), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(BASE / "server.py"),
            "--port",
            str(port),
            "--db",
            str(DB),
            "--no-browser",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    for _ in range(150):
        if proc.poll() is not None:
            raise RuntimeError(proc.stdout.read() if proc.stdout else "server failed")
        try:
            if api(port, "GET", "/api/bootstrap")[0] == 200:
                return proc
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("server timeout")


def stop(proc) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    port = free_port()
    proc = start(port)
    try:
        _, created = api(port, "POST", "/api/projects", {"name": "Файлы"})
        project_id = created["project"]["id"]
        body, content_type = multipart(
            "Файл рядом с базой", [("фото.png", "image/png", PNG)], content_type=""
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/projects/{project_id}/messages",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            message = json.loads(response.read().decode("utf-8"))["message"]
        attachment = message["attachments"][0]
        attachment_id = attachment["id"]
        files_dir = TMP / "tasker_files"
        stored = list(files_dir.rglob("*"))
        assert any(path.is_file() for path in stored), "файл не вынесен рядом с базой"
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/attachments/{attachment_id}", timeout=10
        ) as response:
            assert response.read() == PNG, "файл отдаётся неверно"

        with sqlite3.connect(DB) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(attachments)")]
        assert "data" not in columns and "storage_path" in columns, "старая схема BLOB"
        print("  ok  вложения вынесены в папку, BLOB удалён")

        status, backup = api(port, "POST", "/api/backup")
        assert status == 201
        archive_path = Path(backup["path"])
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert "tasker.db" in names
            assert any(name.startswith("tasker_files/") for name in names)
        print("  ok  ZIP-копия содержит базу и файлы")

        status, _ = api(
            port,
            "POST",
            "/api/sync/folder",
            {"folder": str(DRIVE), "device_name": "Тест-ПК"},
        )
        assert status == 200
        status, sent = api(port, "POST", "/api/sync/send")
        assert status == 200 and (DRIVE / "sync-meta.json").is_file()
        assert (DRIVE / "tasker.db").is_file()
        assert any((DRIVE / "tasker_files").rglob("*.bin"))
        assert sent["manifest"]["version"] == 1
        print("  ok  данные отправлены в папку Диска")

        api(port, "POST", f"/api/projects/{project_id}/tasks", {"title": "После отправки"})
        assert len(api(port, "GET", f"/api/projects/{project_id}/tasks")[1]["tasks"]) == 1
        status, received = api(port, "POST", "/api/sync/receive")
        assert status == 200 and received["manifest"]["version"] == 1
        assert len(api(port, "GET", f"/api/projects/{project_id}/tasks")[1]["tasks"]) == 0
        assert len(api(port, "GET", f"/api/projects/{project_id}/messages")[1]["messages"][0]["attachments"]) == 1
        print("  ok  получение заменило базу и сохранило файлы")

        # Миграция старой базы с BLOB.
        old_dir = TMP / "old"
        old_dir.mkdir()
        old_db = old_dir / "old.db"
        conn = sqlite3.connect(old_db)
        conn.executescript(
            """
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL,
              position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
              position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              done_at TEXT, scheduled_date TEXT);
            CREATE TABLE messages (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE attachments (id TEXT PRIMARY KEY, message_id TEXT NOT NULL,
              project_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL,
              size INTEGER NOT NULL, data BLOB NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        stamp = "2026-01-01T00:00:00+00:00"
        conn.execute("INSERT INTO projects VALUES ('p','P','#4c6ef5',0,?,?)", (stamp, stamp))
        conn.execute("INSERT INTO messages VALUES ('m','p','',?,?)", (stamp, stamp))
        conn.execute(
            "INSERT INTO attachments VALUES ('a','m','p','x.bin','application/octet-stream',3,?,?)",
            (sqlite3.Binary(b"abc"), stamp),
        )
        conn.commit()
        conn.close()
        sys.path.insert(0, str(BASE))
        from tasker.db import Database, SCHEMA_VERSION

        migrated = Database(old_db)
        with migrated.read() as conn:
            row = conn.execute("SELECT storage_path FROM attachments WHERE id='a'").fetchone()
        assert row and row["storage_path"]
        assert (old_dir / "old_files" / row["storage_path"]).read_bytes() == b"abc"
        assert migrated.info()["version"] == SCHEMA_VERSION
        migrated.close()
        print("  ok  старая база мигрирована без потери файла")
        return 0
    finally:
        stop(proc)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        raise
