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
import threading
import time
import traceback
from datetime import date, datetime
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import settings as app_settings
from .db import (
    Database,
    NotFoundError,
    ValidationError,
)
from .filedialog import choose_database_path
from .lifecycle import BrowserLifecycle

VERSION = "1.7.0"

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
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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
        app_dir: str | os.PathLike[str] | None = None,
        default_db_path: str | os.PathLike[str] | None = None,
        settings_path: str | os.PathLike[str] | None = None,
        auto_shutdown: bool = False,
    ):
        self.db = db
        self.web_dir = Path(web_dir).resolve()
        self.verbose = verbose
        self.app_dir = Path(app_dir).resolve() if app_dir else db.path.parent
        self.default_db_path = (
            Path(default_db_path).resolve() if default_db_path else db.path
        )
        self.settings_path = (
            Path(settings_path).resolve()
            if settings_path
            else app_settings.settings_path()
        )
        self.preferences_lock = threading.Lock()
        stored_settings = app_settings.load(self.settings_path)
        self.calendar_preferences = app_settings.normalize_calendar(
            stored_settings.get("calendar")
        )
        self.dialog_lock = threading.Lock()
        self.auto_shutdown = auto_shutdown
        super().__init__(address, TaskerHandler)
        self.lifecycle = BrowserLifecycle(
            self._shutdown_from_browser, enabled=auto_shutdown
        )

    def _shutdown_from_browser(self) -> None:
        print("Вкладка закрыта — завершаю Tasker.")
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        self.shutdown()

    def server_close(self) -> None:
        lifecycle = getattr(self, "lifecycle", None)
        if lifecycle is not None:
            lifecycle.stop()
        super().server_close()

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(
            exc,
            (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError),
        ):
            return
        super().handle_error(request, client_address)


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
                    "info": self._info_payload(),
                    "version": VERSION,
                },
            )

        if segments == ["api", "info"] and method in ("GET", "HEAD"):
            return self.send_json(200, self._info_payload())

        if segments == ["api", "session", "open"] and method == "POST":
            return self._open_session()

        if segments == ["api", "session", "close"] and method == "POST":
            return self._close_session(query)

        if segments == ["api", "session", "events"] and method == "GET":
            return self._stream_session(query)

        if segments == ["api", "settings", "calendar"] and method == "POST":
            return self._save_calendar_settings()

        if segments == ["api", "db", "browse"] and method == "POST":
            return self._browse_database()

        if segments == ["api", "db", "switch"] and method == "POST":
            return self._switch_database()

        if segments == ["api", "calendar"] and method in ("GET", "HEAD"):
            start = self._date_param(query.get("start", [""])[0], "start")
            end = self._date_param(query.get("end", [""])[0], "end")
            if start > end:
                raise ApiError(400, "Начало диапазона позже конца")
            if (end - start).days > 62:
                raise ApiError(400, "Слишком большой диапазон дат")
            return self.send_json(
                200,
                self.server.db.list_calendar(start.isoformat(), end.isoformat()),
            )

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

        if segments == ["api", "projects", "reorder"] and method == "POST":
            data = self._read_json()
            projects = self.server.db.reorder_projects(data.get("ids"))
            return self.send_json(200, {"projects": projects})

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
                        data.get("scheduled_date"),
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
                for key in ("title", "description", "status", "scheduled_date"):
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

    def _info_payload(self) -> dict:
        info = self.server.db.info()
        info["db"]["default_path"] = str(self.server.default_db_path)
        info["db"]["config_path"] = str(self.server.settings_path)
        with self.server.preferences_lock:
            info["preferences"] = dict(self.server.calendar_preferences)
        return info

    @staticmethod
    def _session_id(raw: object) -> str:
        value = raw.strip() if isinstance(raw, str) else ""
        if not SESSION_RE.fullmatch(value):
            raise ApiError(400, "Некорректный идентификатор вкладки")
        return value

    def _open_session(self) -> None:
        data = self._read_json()
        session_id = self._session_id(data.get("session_id"))
        self.server.lifecycle.open(session_id)
        return self.send_json(
            200, {"ok": True, "auto_shutdown": self.server.auto_shutdown}
        )

    def _close_session(self, query: dict) -> None:
        raw = query.get("session_id", [""])[0]
        session_id = self._session_id(raw)
        self.server.lifecycle.close(session_id)
        return self.send_json(200, {"ok": True})

    def _sse_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()

    def _stream_session(self, query: dict) -> None:
        raw = query.get("session_id", [""])[0]
        session_id = self._session_id(raw)
        lifecycle = self.server.lifecycle
        lifecycle.open(session_id)
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._sse_chunk(b": connected\n\n")
            while not lifecycle.stopping:
                time.sleep(2)
                self._sse_chunk(b": ping\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            lifecycle.close(session_id)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def _save_calendar_settings(self) -> None:
        data = self._read_json()
        with self.server.preferences_lock:
            preferences = dict(self.server.calendar_preferences)
        for key in app_settings.DEFAULT_CALENDAR:
            if key not in data:
                continue
            if not isinstance(data[key], bool):
                raise ApiError(400, f"Настройка {key} должна быть логической")
            preferences[key] = data[key]
        try:
            stored = app_settings.load(self.server.settings_path)
            stored["calendar"] = preferences
            app_settings.save(stored, self.server.settings_path)
        except OSError as exc:
            raise ApiError(
                500, f"Не удалось сохранить настройки: {exc}"
            ) from exc
        with self.server.preferences_lock:
            self.server.calendar_preferences = dict(preferences)
        return self.send_json(200, {"preferences": preferences})

    def _browse_database(self) -> None:
        try:
            with self.server.dialog_lock:
                selected = choose_database_path(str(self.server.db.path))
        except OSError as exc:
            raise ApiError(501, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError(500, f"Не удалось открыть диалог: {exc}") from exc
        if not selected:
            return self.send_json(200, {"cancelled": True})
        path = Path(selected).expanduser()
        if not path.is_absolute():
            path = self.server.app_dir / path
        return self.send_json(
            200, {"cancelled": False, "path": str(path.resolve())}
        )

    def _switch_database(self) -> None:
        data = self._read_json()
        raw_path = data.get("path")
        if raw_path is None or (isinstance(raw_path, str) and not raw_path.strip()):
            target = self.server.default_db_path
        else:
            if not isinstance(raw_path, str):
                raise ApiError(400, "Некорректный путь к базе")
            target = Path(os.path.expandvars(raw_path.strip())).expanduser()
            if not target.is_absolute():
                target = self.server.app_dir / target
            target = target.resolve()
        try:
            self.server.db.switch_path(target)
        except ValidationError as exc:
            raise ApiError(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError(400, f"Не удалось открыть базу: {exc}") from exc

        warning = None
        try:
            stored = app_settings.load(self.server.settings_path)
            if target == self.server.default_db_path:
                stored.pop("db_path", None)
            else:
                stored["db_path"] = str(self.server.db.path)
            app_settings.save(stored, self.server.settings_path)
        except OSError as exc:
            warning = f"База открыта, но путь не сохранился в настройках: {exc}"

        return self.send_json(
            200,
            {
                "ok": True,
                "path": str(self.server.db.path),
                "info": self._info_payload(),
                "warning": warning,
            },
        )

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

    @staticmethod
    def _date_param(value: str, name: str) -> date:
        if not value:
            raise ApiError(400, f"Не указан параметр {name}")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ApiError(400, "Некорректная дата в запросе") from None

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
    app_dir: str | os.PathLike[str] | None = None,
    default_db_path: str | os.PathLike[str] | None = None,
    settings_path: str | os.PathLike[str] | None = None,
    auto_shutdown: bool = False,
) -> TaskerServer:
    return TaskerServer(
        (host, port),
        db,
        web_dir,
        verbose,
        app_dir=app_dir,
        default_db_path=default_db_path,
        settings_path=settings_path,
        auto_shutdown=auto_shutdown,
    )
