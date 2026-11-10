"""HTTP-слой Tasker: REST API и раздача интерфейса."""

from __future__ import annotations

import email.policy
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .db import (
    Database,
    NotFoundError,
    ValidationError,
)

VERSION = "1.0.0"

MAX_JSON_BODY = 2 * 1024 * 1024
MAX_UPLOAD_BODY = 120 * 1024 * 1024

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

INLINE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
}

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _disposition(kind: str, filename: str) -> str:
    fallback = filename.encode("ascii", "replace").decode("ascii")
    fallback = re.sub(r'["\\\r\n]', "_", fallback) or "file"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


class TaskerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        db: Database,
        web_dir: str | os.PathLike[str],
        verbose: bool = False,
    ):
        self.db = db
        self.web_dir = Path(web_dir).resolve()
        self.verbose = verbose
        super().__init__(address, TaskerHandler)


class TaskerHandler(BaseHTTPRequestHandler):
    server: TaskerServer
    server_version = f"Tasker/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # маршрутизация
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_json(405, {"error": "Метод не поддерживается"})

    def _handle(self, method: str) -> None:
        try:
            self._check_host()
            if method in ("POST", "PATCH", "DELETE", "PUT"):
                self._check_origin()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if not path.startswith("/api") or path == "/api":
                if method not in ("GET", "HEAD"):
                    raise ApiError(405, "Метод не поддерживается")
                self._serve_static(path)
                return
            segments = [segment for segment in path.split("/") if segment]
            self._route_api(method, segments, parse_qs(parsed.query))
        except ApiError as exc:
            self._send_error_json(exc.status, exc.message)
        except ValidationError as exc:
            self._send_error_json(400, str(exc))
        except NotFoundError as exc:
            self._send_error_json(404, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._send_error_json(500, "Внутренняя ошибка сервера")

    def _route_api(self, method: str, segments: list[str], query: dict) -> None:
        if segments == ["api", "bootstrap"] and method in ("GET", "HEAD"):
            return self.send_json(
                200,
                {
                    "projects": self.server.db.list_projects(),
                    "info": self.server.db.info(),
                    "version": VERSION,
                },
            )

        if segments == ["api", "info"] and method in ("GET", "HEAD"):
            return self.send_json(200, self.server.db.info())

        if segments == ["api", "backup"] and method == "POST":
            path = self.server.db.create_backup()
            return self.send_json(
                201,
                {
                    "ok": True,
                    "filename": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                },
            )

        if segments == ["api", "backup", "download"] and method in ("GET", "HEAD"):
            return self._download_snapshot()

        if segments == ["api", "open-data-folder"] and method == "POST":
            return self._open_data_folder()

        if segments == ["api", "projects"]:
            if method in ("GET", "HEAD"):
                return self.send_json(200, {"projects": self.server.db.list_projects()})
            if method == "POST":
                data = self._read_json()
                project = self.server.db.create_project(
                    data.get("name"), data.get("color")
                )
                return self.send_json(201, {"project": project})

        if len(segments) == 3 and segments[:2] == ["api", "projects"]:
            project_id = self._valid_id(segments[2])
            if method in ("GET", "HEAD"):
                return self.send_json(
                    200, {"project": self.server.db.get_project(project_id)}
                )
            if method == "PATCH":
                data = self._read_json()
                kwargs = {}
                if "name" in data:
                    kwargs["name"] = data["name"]
                if "color" in data:
                    kwargs["color"] = data["color"]
                if not kwargs:
                    raise ApiError(400, "Нет изменений")
                project = self.server.db.update_project(project_id, **kwargs)
                return self.send_json(200, {"project": project})
            if method == "DELETE":
                self.server.db.delete_project(project_id)
                return self.send_json(200, {"ok": True})

        if len(segments) == 4 and segments[:2] == ["api", "projects"]:
            project_id = self._valid_id(segments[2])
            resource = segments[3]
            if resource == "tasks":
                if method in ("GET", "HEAD"):
                    return self.send_json(
                        200,
                        {
                            "tasks": self.server.db.list_tasks(project_id),
                            "project": self.server.db.get_project(project_id),
                        },
                    )
                if method == "POST":
                    data = self._read_json()
                    result = self.server.db.create_task(
                        project_id,
                        data.get("title"),
                        data.get("description", ""),
                        data.get("status", "urgent"),
                    )
                    return self.send_json(201, result)
            if resource == "messages":
                if method in ("GET", "HEAD"):
                    limit = query.get("limit", ["100"])[0]
                    before = query.get("before", [None])[0]
                    return self.send_json(
                        200,
                        self.server.db.list_messages(project_id, limit, before),
                    )
                if method == "POST":
                    return self._create_message(project_id)

        if len(segments) == 3 and segments[:2] == ["api", "tasks"]:
            task_id = self._valid_id(segments[2])
            if method == "PATCH":
                data = self._read_json()
                kwargs = {}
                for key in ("title", "description", "status"):
                    if key in data:
                        kwargs[key] = data[key]
                if not kwargs:
                    raise ApiError(400, "Нет изменений")
                return self.send_json(
                    200, self.server.db.update_task(task_id, **kwargs)
                )
            if method == "DELETE":
                return self.send_json(200, self.server.db.delete_task(task_id))

        if (
            len(segments) == 4
            and segments[:2] == ["api", "tasks"]
            and segments[3] == "move"
            and method == "POST"
        ):
            task_id = self._valid_id(segments[2])
            data = self._read_json()
            if "status" not in data:
                raise ApiError(400, "Не указана колонка")
            return self.send_json(
                200,
                self.server.db.move_task(task_id, data["status"], data.get("index")),
            )

        if len(segments) == 3 and segments[:2] == ["api", "messages"]:
            message_id = self._valid_id(segments[2])
            if method == "PATCH":
                data = self._read_json()
                if "text" not in data:
                    raise ApiError(400, "Не указан текст заметки")
                return self.send_json(
                    200, self.server.db.update_message(message_id, data["text"])
                )
            if method == "DELETE":
                return self.send_json(
                    200, self.server.db.delete_message(message_id)
                )

        if (
            len(segments) == 3
            and segments[:2] == ["api", "attachments"]
            and method in ("GET", "HEAD")
        ):
            return self._send_attachment(self._valid_id(segments[2]), query)

        raise ApiError(404, "Не найдено")

    # ------------------------------------------------------------------
    # API: заметки
    # ------------------------------------------------------------------
    def _create_message(self, project_id: str) -> None:
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("multipart/form-data"):
            text, files = self._read_multipart()
        elif content_type.lower().startswith("application/json"):
            data = self._read_json()
            text = data.get("text", "")
            files = []
        else:
            raise ApiError(415, "Ожидается multipart/form-data или JSON")
        result = self.server.db.create_message(project_id, text, files)
        self.send_json(201, result)

    def _read_multipart(self) -> tuple[str, list[dict]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ApiError(415, "Ожидается multipart/form-data")
        body = self._read_body(MAX_UPLOAD_BODY)
        raw = (
            b"Content-Type: "
            + content_type.encode("latin-1", "replace")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
        try:
            message = BytesParser(policy=email.policy.default).parsebytes(raw)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(400, "Не удалось разобрать загруженные данные") from exc
        if not message.is_multipart():
            raise ApiError(400, "Не удалось разобрать загруженные данные")

        text = ""
        files: list[dict] = []
        total = 0
        for part in message.iter_parts():
            filename = part.get_filename()
            field_name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True) or b""
            if filename is not None:
                if len(files) >= 12:
                    raise ApiError(413, "Слишком много файлов (максимум 12)")
                if len(payload) > 40 * 1024 * 1024:
                    raise ApiError(413, "Файл больше 40 МБ")
                total += len(payload)
                if total > 96 * 1024 * 1024:
                    raise ApiError(413, "Суммарный размер файлов больше 96 МБ")
                mime = part.get_content_type() or "application/octet-stream"
                if mime == "application/octet-stream":
                    guessed, _ = mimetypes.guess_type(filename)
                    if guessed:
                        mime = guessed
                files.append({"filename": filename, "mime": mime, "data": payload})
            elif field_name == "text":
                text = payload.decode("utf-8", "replace")
        return text, files

    # ------------------------------------------------------------------
    # API: вложения, бэкапы
    # ------------------------------------------------------------------
    def _send_attachment(self, attachment_id: str, query: dict) -> None:
        row = self.server.db.get_attachment(attachment_id)
        data = row["data"]
        download = query.get("download", ["0"])[0] not in ("0", "", "false", "False")
        mime = str(row["mime"])
        inline = not download and mime in INLINE_MIME_TYPES
        self.send_response(200)
        self.send_header("Content-Type", mime if inline else "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            _disposition("inline" if inline else "attachment", row["filename"]),
        )
        self.send_header("Cache-Control", "private, max-age=86400, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _download_snapshot(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        filename = f"tasker-{stamp}.db"
        with tempfile.TemporaryDirectory(prefix="tasker-snapshot-") as tmp:
            snapshot = Path(tmp) / "tasker.db"
            self.server.db.backup_to(snapshot)
            size = snapshot.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", _disposition("attachment", filename))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                with snapshot.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile, 256 * 1024)

    def _open_data_folder(self) -> None:
        path = self.server.db.data_dir
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            raise ApiError(500, f"Не удалось открыть папку: {exc}") from exc
        self.send_json(200, {"ok": True, "path": str(path)})

    # ------------------------------------------------------------------
    # статика
    # ------------------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        if path in ("", "/", "/index.html"):
            target = self.server.web_dir / "index.html"
        else:
            relative = path.lstrip("/")
            target = (self.server.web_dir / relative).resolve()
            web_root = self.server.web_dir
            if target != web_root and web_root not in target.parents:
                raise ApiError(404, "Не найдено")
            if target.is_dir():
                target = target / "index.html"
        if not target.is_file():
            if "." not in Path(path).name:
                target = self.server.web_dir / "index.html"
            else:
                raise ApiError(404, "Не найдено")

        try:
            stat = target.stat()
            data = target.read_bytes()
        except OSError as exc:
            raise ApiError(404, "Не найдено") from exc

        content_type = STATIC_TYPES.get(target.suffix.lower())
        if content_type is None:
            guessed, _ = mimetypes.guess_type(str(target))
            content_type = guessed or "application/octet-stream"

        etag = f'W/"{stat.st_mtime_ns}-{stat.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ------------------------------------------------------------------
    # общие помощники
    # ------------------------------------------------------------------
    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        if status >= 400:
            # Тело запроса могло остаться непрочитанным — не пытаемся
            # продолжать keep-alive соединение после ошибки.
            self.close_connection = True
        try:
            self.send_json(status, {"error": message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = self.headers.get("Host")
        allowed = {f"http://{host}", f"https://{host}"} if host else set()
        if origin not in allowed:
            raise ApiError(403, "Запрос с чужого источника запрещён")

    def _check_host(self) -> None:
        """Не даём DNS-rebinding-сайтам обращаться к локальному API."""
        server_host = str(self.server.server_address[0])
        if server_host not in ("127.0.0.1", "::1", "localhost"):
            return
        host_header = self.headers.get("Host", "")
        if host_header.startswith("["):
            hostname = host_header[1 : host_header.find("]")] if "]" in host_header else host_header
        else:
            hostname = host_header.rsplit(":", 1)[0]
        if hostname.lower() not in ("127.0.0.1", "localhost", "::1"):
            raise ApiError(403, "Некорректный адрес хоста")

    @staticmethod
    def _valid_id(value: str) -> str:
        if not ID_RE.fullmatch(value or ""):
            raise ApiError(404, "Не найдено")
        return value

    def _content_length(self, limit: int) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ApiError(411, "Не указан размер запроса")
        try:
            length = int(raw)
        except ValueError:
            raise ApiError(400, "Некорректный размер запроса") from None
        if length < 0:
            raise ApiError(400, "Некорректный размер запроса")
        if length > limit:
            self.close_connection = True
            raise ApiError(413, "Запрос слишком большой")
        return length

    def _read_body(self, limit: int) -> bytes:
        length = self._content_length(limit)
        if not length:
            return b""
        data = self.rfile.read(length)
        if len(data) != length:
            raise ApiError(400, "Некорректный запрос")
        return data

    def _read_json(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise ApiError(415, "Ожидается JSON")
        raw = self._read_body(MAX_JSON_BODY)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "Некорректный JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Ожидается JSON-объект")
        return data

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if self.server.verbose:
            super().log_request(code, size)
            return
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        if status >= 400:
            super().log_request(code, size)

    def log_error(self, format: str, *args) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_error(format, *args)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_message(format, *args)


def create_server(
    host: str,
    port: int,
    db: Database,
    web_dir: str | os.PathLike[str],
    verbose: bool = False,
) -> TaskerServer:
    return TaskerServer((host, port), db, web_dir, verbose)
