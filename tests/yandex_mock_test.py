#!/usr/bin/env python3
"""Проверка REST-клиента Яндекс Диска на локальном mock-сервере."""

from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tasker import yandex
from tasker.db import Database

FILES: dict[str, dict[str, object]] = {}


def md5(data: bytes) -> str:
    import hashlib

    return hashlib.md5(data).hexdigest()


def resource(path: str):
    item = FILES.get(path)
    if not item:
        return None
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": item["type"],
        "size": len(item.get("data", b"")),
        "md5": md5(item.get("data", b"")),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code: int, payload: dict, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, code: int, headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path == "/v1/disk/":
            return self._json(200, {"total_space": 100000000, "used_space": 1000, "trash_size": 0})
        if path == "/v1/disk/resources":
            target = query.get("path", [""])[0]
            item = resource(target)
            if item is None and target == "app:/":
                item = {"name": "app", "path": "app:/", "type": "dir"}
            if item is None:
                return self._json(404, {"description": "not found"})
            if item["type"] == "dir":
                prefix = target.rstrip("/") + "/"
                children = [
                    resource(key)
                    for key in FILES
                    if key.startswith(prefix) and "/" not in key[len(prefix):]
                ]
                item = dict(item, _embedded={"items": children, "total": len(children)})
            return self._json(200, item)
        if path == "/v1/disk/resources/upload":
            target = query.get("path", [""])[0]
            href = f"http://127.0.0.1:{self.server.server_port}/upload?path={urllib.parse.quote(target)}"
            return self._json(200, {"href": href, "method": "PUT", "templated": False})
        if path == "/v1/disk/resources/download":
            target = query.get("path", [""])[0]
            href = f"http://127.0.0.1:{self.server.server_port}/download?path={urllib.parse.quote(target)}"
            return self._json(200, {"href": href, "method": "GET", "templated": False})
        if path == "/download":
            target = query.get("path", [""])[0]
            item = FILES.get(target)
            if not item or item["type"] != "file":
                return self._json(404, {"description": "not found"})
            data = item["data"]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json(404, {"description": "not found"})

    def do_PUT(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/v1/disk/resources":
            target = query.get("path", [""])[0]
            FILES[target] = {"type": "dir"}
            return self._empty(201)
        if parsed.path == "/upload":
            target = query.get("path", [""])[0]
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length)
            FILES[target] = {"type": "file", "data": data}
            return self._empty(201)
        return self._json(404, {"description": "not found"})

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        target = query.get("path", [""])[0]
        FILES.pop(target, None)
        return self._empty(204)


def main():
    global FILES
    FILES = {"app:/": {"type": "dir"}}
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1/disk"
    old_api = yandex.API
    yandex.API = base
    try:
        with TemporaryDirectory(prefix="tasker-yandex-test-") as tmp:
            root = Path(tmp)
            db = Database(root / "local.db")
            project = db.create_project("Проект", "#4c6ef5")
            db.create_task(project["id"], "Задача", "Описание", "urgent", "2026-11-02", "2026-11-06")
            db.create_message(
                project["id"],
                "Заметка с файлом",
                [{"filename": "a.txt", "mime": "text/plain", "data": b"hello"}],
            )
            sent = yandex.send(db, "token", "TestPC")
            assert sent["manifest"]["version"] == 1
            assert yandex.remote_manifest("token")["version"] == 1
            assert "app:/tasker.db" in FILES
            assert "app:/sync-meta.json" in FILES
            second = Database(root / "second.db")
            yandex.receive(second, "token")
            tasks = second.list_tasks(project["id"])
            assert tasks and tasks[0]["title"] == "Задача"
            assert tasks[0]["scheduled_end_date"] == "2026-11-06"
            assert second.info()["stats"]["attachments"] == 1
            db.close()
            second.close()
        print("Yandex mock: отправка и получение работают")
    finally:
        yandex.API = old_api
        server.shutdown()


if __name__ == "__main__":
    main()
