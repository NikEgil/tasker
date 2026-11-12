"""HTTP-слой Tasker: REST API и раздача интерфейса."""

from __future__ import annotations

import email.policy
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import settings as app_settings
from . import sync as app_sync
from . import gdrive
from . import yandex
from .db import (
    Database,
    NotFoundError,
    ValidationError,
)
from .filedialog import choose_database_path, choose_directory
from .lifecycle import BrowserLifecycle

VERSION = "1.11.0"

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
        self.sync_folder = str(stored_settings.get("sync_folder") or "")
        self.sync_mode = str(stored_settings.get("sync_mode") or "folder")
        if self.sync_mode not in ("folder", "drive", "yandex"):
            self.sync_mode = "folder"
        self.device_name = str(
            stored_settings.get("device_name") or socket.gethostname() or "Tasker"
        )
        self.last_sync = (
            stored_settings.get("last_sync")
            if isinstance(stored_settings.get("last_sync"), dict)
            else {}
        )
        self.drive_client_id = str(stored_settings.get("drive_client_id") or "")
        self.drive_client_secret = str(
            stored_settings.get("drive_client_secret") or ""
        )
        self.drive_refresh_token = str(
            stored_settings.get("drive_refresh_token") or ""
        )
        self.drive_access_token = str(
            stored_settings.get("drive_access_token") or ""
        )
        try:
            self.drive_token_expires = float(
                stored_settings.get("drive_token_expires") or 0
            )
        except (TypeError, ValueError):
            self.drive_token_expires = 0.0
        self.drive_folder_id = str(
            stored_settings.get("drive_folder_id") or ""
        )
        self.drive_files_id = str(stored_settings.get("drive_files_id") or "")
        self.yandex_token = str(stored_settings.get("yandex_token") or "")
        self.yandex_connected = bool(
            stored_settings.get("yandex_connected") and self.yandex_token
        )
        self.yandex_total_space = int(
            stored_settings.get("yandex_total_space") or 0
        )
        self.yandex_used_space = int(
            stored_settings.get("yandex_used_space") or 0
        )
        self.yandex_trash_size = int(
            stored_settings.get("yandex_trash_size") or 0
        )
        self.yandex_last_check = str(stored_settings.get("yandex_last_check") or "")
        self.oauth_pending: dict[str, str] | None = None
        self.sync_lock = threading.RLock()
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
            if path == "/oauth2callback" and method in ("GET", "HEAD"):
                return self._oauth_callback(parse_qs(parsed.query))
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

        if segments == ["api", "sync", "status"] and method in ("GET", "HEAD"):
            payload = self._sync_payload()
            if query.get("refresh"):
                self._refresh_remote_status(payload)
            return self.send_json(200, payload)

        if segments == ["api", "sync", "folder"] and method == "POST":
            return self._save_sync_settings()

        if segments == ["api", "sync", "browse"] and method == "POST":
            return self._browse_sync_folder()

        if segments == ["api", "sync", "send"] and method == "POST":
            return self._sync_send()

        if segments == ["api", "sync", "receive"] and method == "POST":
            return self._sync_receive()

        if segments == ["api", "drive", "settings"] and method == "POST":
            return self._save_drive_settings()

        if segments == ["api", "drive", "auth", "start"] and method == "POST":
            return self._start_drive_auth()

        if segments == ["api", "drive", "disconnect"] and method == "POST":
            return self._disconnect_drive()

        if segments == ["api", "yandex", "connect"] and method == "POST":
            return self._connect_yandex()

        if segments == ["api", "yandex", "disconnect"] and method == "POST":
            return self._disconnect_yandex()

        if segments == ["api", "db", "browse"] and method == "POST":
            return self._browse_database()

        if segments == ["api", "db", "switch"] and method == "POST":
            return self._switch_database()

        if segments == ["api", "calendar", "order"] and method == "POST":
            return self._reorder_calendar()

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
                        data.get("scheduled_end_date"),
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
                for key in (
                    "title",
                    "description",
                    "status",
                    "scheduled_date",
                    "scheduled_end_date",
                ):
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
        info["sync"] = self._sync_payload()
        return info

    @staticmethod
    def _remote_summary(manifest: dict | None) -> dict | None:
        if not isinstance(manifest, dict):
            return None
        return {
            key: manifest[key]
            for key in ("version", "device", "updated_at", "sha256", "size", "schema")
            if key in manifest
        }

    def _refresh_remote_status(self, payload: dict) -> None:
        mode = self.server.sync_mode
        if mode == "folder":
            return
        payload["remote"] = None
        try:
            manifest = None
            if mode == "drive":
                if not self.server.drive_refresh_token:
                    return
                with self.server.sync_lock:
                    client = gdrive.DriveClient(self._drive_access_token())
                    manifest = gdrive.remote_manifest(
                        client, self.server.drive_folder_id or None
                    )
            elif mode == "yandex":
                if not self.server.yandex_token:
                    return
                with self.server.sync_lock:
                    manifest = yandex.remote_manifest(self.server.yandex_token)
            payload["remote"] = self._remote_summary(manifest)
        except ApiError as exc:
            payload["remote_error"] = exc.message
        except (gdrive.DriveError, yandex.YandexError, OSError) as exc:
            payload["remote_error"] = str(exc)

    def _sync_payload(self) -> dict:
        payload = app_sync.status(self.server.sync_folder)
        payload["mode"] = self.server.sync_mode
        payload["device_name"] = self.server.device_name
        payload["last_sync"] = dict(self.server.last_sync)
        payload["drive"] = {
            "connected": bool(self.server.drive_refresh_token),
            "client_id": self.server.drive_client_id,
            "client_secret_set": bool(self.server.drive_client_secret),
            "folder_id": self.server.drive_folder_id,
            "files_id": self.server.drive_files_id,
        }
        payload["yandex"] = {
            "connected": bool(self.server.yandex_connected),
            "token_set": bool(self.server.yandex_token),
            "total_space": int(self.server.yandex_total_space),
            "used_space": int(self.server.yandex_used_space),
            "trash_size": int(self.server.yandex_trash_size),
            "last_check": self.server.yandex_last_check,
        }
        payload["local"] = {
            "db_version": self.server.db.info().get("version"),
            "revision": self.server.db.info().get("db", {}).get("revision"),
        }
        return payload

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
        self._drain_body()
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

    def _reorder_calendar(self) -> None:
        data = self._read_json()
        task_ids = data.get("ids")
        self.server.db.reorder_calendar(task_ids)
        return self.send_json(200, {"ok": True})

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

    def _save_sync_settings(self) -> None:
        data = self._read_json()
        folder = str(data.get("folder") or "").strip()
        mode = str(data.get("mode") or self.server.sync_mode).strip()
        if mode not in ("folder", "drive", "yandex"):
            raise ApiError(400, "Неизвестный способ синхронизации")
        device = " ".join(str(data.get("device_name") or "").split())[:80]
        if folder:
            try:
                folder = str(Path(folder).expanduser().resolve())
            except OSError as exc:
                raise ApiError(400, f"Некорректный путь: {exc}") from exc
            if Path(folder).exists() and not Path(folder).is_dir():
                raise ApiError(400, "Выбрана папка синхронизации, но это файл")
        if not device:
            device = socket.gethostname() or "Tasker"
        with self.server.sync_lock:
            stored = app_settings.load(self.server.settings_path)
            stored["sync_folder"] = folder
            stored["sync_mode"] = mode
            stored["device_name"] = device
            app_settings.save(stored, self.server.settings_path)
            self.server.sync_folder = folder
            self.server.sync_mode = mode
            self.server.device_name = device
        return self.send_json(200, {"sync": self._sync_payload()})

    def _browse_sync_folder(self) -> None:
        self._drain_body()
        try:
            with self.server.dialog_lock:
                selected = choose_directory(self.server.sync_folder or None)
        except OSError as exc:
            raise ApiError(501, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError(500, f"Не удалось открыть диалог: {exc}") from exc
        if not selected:
            return self.send_json(200, {"cancelled": True})
        return self.send_json(200, {"path": selected})

    def _sync_send(self) -> None:
        self._drain_body()
        if self.server.sync_mode == "yandex":
            return self._yandex_send()
        if self.server.sync_mode == "drive":
            return self._drive_send()
        folder = self.server.sync_folder
        if not folder:
            raise ApiError(400, "Сначала выберите папку Google Диска")
        try:
            with self.server.sync_lock:
                result = app_sync.send(
                    self.server.db, folder, self.server.device_name
                )
                self._remember_sync(result.get("manifest") or {})
        except ValidationError as exc:
            raise ApiError(400, str(exc)) from exc
        except OSError as exc:
            raise ApiError(500, f"Не удалось отправить данные: {exc}") from exc
        return self.send_json(
            200,
            {
                "ok": True,
                "folder": result.get("folder"),
                "manifest": result.get("manifest"),
                "sync": self._sync_payload(),
            },
        )

    def _sync_receive(self) -> None:
        self._drain_body()
        if self.server.sync_mode == "yandex":
            return self._yandex_receive()
        if self.server.sync_mode == "drive":
            return self._drive_receive()
        folder = self.server.sync_folder
        if not folder:
            raise ApiError(400, "Сначала выберите папку Google Диска")
        try:
            with self.server.sync_lock:
                result = app_sync.receive(self.server.db, folder)
                self._remember_sync(result.get("manifest") or {})
        except ValidationError as exc:
            raise ApiError(400, str(exc)) from exc
        except OSError as exc:
            raise ApiError(500, f"Не удалось скачать данные: {exc}") from exc
        return self.send_json(
            200,
            {
                "ok": True,
                "folder": result.get("folder"),
                "manifest": result.get("manifest"),
                "info": result.get("info"),
                "sync": self._sync_payload(),
            },
        )

    def _save_settings_fields(self, **fields) -> None:
        stored = app_settings.load(self.server.settings_path)
        stored.update(fields)
        app_settings.save(stored, self.server.settings_path)

    def _save_drive_settings(self) -> None:
        data = self._read_json()
        client_id = str(data.get("client_id") or "").strip()
        client_secret = str(data.get("client_secret") or "").strip()
        if not client_id:
            raise ApiError(400, "Укажите OAuth Client ID")
        if not client_secret:
            client_secret = self.server.drive_client_secret
        with self.server.sync_lock:
            self._save_settings_fields(
                sync_mode="drive",
                drive_client_id=client_id,
                drive_client_secret=client_secret,
            )
            self.server.sync_mode = "drive"
            self.server.drive_client_id = client_id
            self.server.drive_client_secret = client_secret
        return self.send_json(200, {"sync": self._sync_payload()})

    def _start_drive_auth(self) -> None:
        data = self._read_json()
        client_id = str(data.get("client_id") or self.server.drive_client_id).strip()
        client_secret = str(
            data.get("client_secret") or self.server.drive_client_secret
        ).strip()
        if not client_id:
            raise ApiError(400, "Укажите OAuth Client ID из Google Cloud Console")
        redirect_uri = f"http://127.0.0.1:{self.server.server_address[1]}/oauth2callback"
        state = secrets.token_urlsafe(32)
        verifier = gdrive.make_verifier()
        with self.server.sync_lock:
            self._save_settings_fields(
                sync_mode="drive",
                drive_client_id=client_id,
                drive_client_secret=client_secret,
                drive_refresh_token="",
                drive_access_token="",
                drive_token_expires=0,
                drive_folder_id="",
                drive_files_id="",
            )
            self.server.sync_mode = "drive"
            self.server.drive_client_id = client_id
            self.server.drive_client_secret = client_secret
            self.server.drive_refresh_token = ""
            self.server.drive_access_token = ""
            self.server.drive_token_expires = 0.0
            self.server.drive_folder_id = ""
            self.server.drive_files_id = ""
            self.server.oauth_pending = {
                "state": state,
                "verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        url = gdrive.auth_url(client_id, redirect_uri, state, verifier)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return self.send_json(
            200,
            {
                "url": url,
                "redirect_uri": redirect_uri,
            },
        )

    def _oauth_callback(self, query: dict) -> None:
        error = query.get("error", [""])[0]
        if error:
            return self._send_html(
                "Не удалось подключить Google Диск",
                "Доступ не был предоставлен. Закройте окно и попробуйте снова.",
            )
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        pending = self.server.oauth_pending
        if not pending or not state or not secrets.compare_digest(
            str(pending.get("state", "")), state
        ):
            return self._send_html(
                "Ошибка подключения",
                "Сессия авторизации устарела. Начните подключение заново.",
            )
        try:
            tokens = gdrive.exchange_code(
                self.server.drive_client_id,
                self.server.drive_client_secret,
                code,
                pending["redirect_uri"],
                pending["verifier"],
            )
        except gdrive.DriveError as exc:
            return self._send_html("Ошибка Google", str(exc))
        refresh_token = tokens.get("refresh_token") or self.server.drive_refresh_token
        access_token = tokens.get("access_token", "")
        expires = time.time() + int(tokens.get("expires_in", 3600)) - 60
        with self.server.sync_lock:
            self._save_settings_fields(
                drive_refresh_token=refresh_token,
                drive_access_token=access_token,
                drive_token_expires=expires,
                drive_folder_id="",
                drive_files_id="",
            )
            self.server.drive_refresh_token = refresh_token
            self.server.drive_access_token = access_token
            self.server.drive_token_expires = expires
            self.server.drive_folder_id = ""
            self.server.drive_files_id = ""
            self.server.oauth_pending = None
        return self._send_html(
            "Google Диск подключён",
            "Можно закрыть это окно и вернуться в Tasker.",
        )

    def _disconnect_drive(self) -> None:
        self._drain_body()
        with self.server.sync_lock:
            self._save_settings_fields(
                drive_refresh_token="",
                drive_access_token="",
                drive_token_expires=0,
                drive_folder_id="",
                drive_files_id="",
            )
            self.server.drive_refresh_token = ""
            self.server.drive_access_token = ""
            self.server.drive_token_expires = 0.0
            self.server.drive_folder_id = ""
            self.server.drive_files_id = ""
        return self.send_json(200, {"sync": self._sync_payload()})

    def _drive_access_token(self) -> str:
        if self.server.drive_access_token and self.server.drive_token_expires > time.time() + 60:
            return self.server.drive_access_token
        if not self.server.drive_refresh_token:
            raise ApiError(401, "Сначала подключите Google Диск")
        try:
            tokens = gdrive.refresh_access_token(
                self.server.drive_client_id,
                self.server.drive_client_secret,
                self.server.drive_refresh_token,
            )
        except gdrive.DriveError as exc:
            raise ApiError(401, f"Не удалось обновить доступ: {exc}") from exc
        access = tokens.get("access_token", "")
        expires = time.time() + int(tokens.get("expires_in", 3600)) - 60
        with self.server.sync_lock:
            self._save_settings_fields(
                drive_access_token=access,
                drive_token_expires=expires,
            )
            self.server.drive_access_token = access
            self.server.drive_token_expires = expires
        return access

    def _drive_client(self) -> gdrive.DriveClient:
        return gdrive.DriveClient(self._drive_access_token())

    def _drive_send(self) -> None:
        try:
            with self.server.sync_lock:
                client = self._drive_client()
                folder_id, files_id = gdrive.ensure_folders(
                    client,
                    self.server.drive_folder_id,
                    self.server.drive_files_id,
                )
                result = gdrive.send(
                    self.server.db,
                    client,
                    folder_id,
                    files_id,
                    self.server.device_name,
                )
                self.server.drive_folder_id = folder_id
                self.server.drive_files_id = files_id
                self._save_settings_fields(
                    drive_folder_id=folder_id, drive_files_id=files_id
                )
                self._remember_sync(result.get("manifest") or {})
        except gdrive.AuthRequired as exc:
            raise ApiError(401, str(exc)) from exc
        except gdrive.DriveError as exc:
            raise ApiError(502, f"Google Drive: {exc}") from exc
        return self.send_json(
            200,
            {
                "ok": True,
                "provider": "drive",
                "manifest": result.get("manifest"),
                "sync": self._sync_payload(),
            },
        )

    def _drive_receive(self) -> None:
        try:
            with self.server.sync_lock:
                client = self._drive_client()
                folder_id = self.server.drive_folder_id
                if not folder_id:
                    root = gdrive.find_root(client)
                    folder_id = root["id"] if root else ""
                if not folder_id:
                    raise gdrive.DriveError("Папка Tasker на Google Диске не найдена")
                result = gdrive.receive(self.server.db, client, folder_id)
                self.server.drive_folder_id = folder_id
                files_id = (result.get("manifest") or {}).get("files_id") or ""
                if files_id:
                    self.server.drive_files_id = files_id
                self._save_settings_fields(
                    drive_folder_id=folder_id,
                    drive_files_id=self.server.drive_files_id,
                )
                self._remember_sync(result.get("manifest") or {})
        except gdrive.AuthRequired as exc:
            raise ApiError(401, str(exc)) from exc
        except gdrive.DriveError as exc:
            raise ApiError(502, f"Google Drive: {exc}") from exc
        return self.send_json(
            200,
            {
                "ok": True,
                "provider": "drive",
                "manifest": result.get("manifest"),
                "info": result.get("info"),
                "sync": self._sync_payload(),
            },
        )

    def _connect_yandex(self) -> None:
        data = self._read_json()
        token = str(data.get("token") or "").strip()
        if not token:
            token = self.server.yandex_token
        if not token:
            raise ApiError(400, "Вставьте OAuth-токен Яндекс Диска")
        try:
            client = yandex.YandexClient(token)
            try:
                info = client.disk_info()
            except yandex.AuthRequired:
                raise
            except yandex.YandexError:
                info = {}
            client.ensure_folder(yandex.FILES_PATH)
        except yandex.AuthRequired as exc:
            raise ApiError(401, str(exc)) from exc
        except yandex.YandexError as exc:
            raise ApiError(502, f"Яндекс Диск: {exc}") from exc
        checked_at = datetime.now().isoformat(timespec="seconds")
        with self.server.sync_lock:
            self._save_settings_fields(
                sync_mode="yandex",
                yandex_token=token,
                yandex_connected=True,
                yandex_total_space=int(info.get("total_space") or 0),
                yandex_used_space=int(info.get("used_space") or 0),
                yandex_trash_size=int(info.get("trash_size") or 0),
                yandex_last_check=checked_at,
            )
            self.server.sync_mode = "yandex"
            self.server.yandex_token = token
            self.server.yandex_connected = True
            self.server.yandex_total_space = int(info.get("total_space") or 0)
            self.server.yandex_used_space = int(info.get("used_space") or 0)
            self.server.yandex_trash_size = int(info.get("trash_size") or 0)
            self.server.yandex_last_check = checked_at
        return self.send_json(
            200,
            {
                "ok": True,
                "yandex": {
                    "total_space": self.server.yandex_total_space,
                    "used_space": self.server.yandex_used_space,
                    "trash_size": self.server.yandex_trash_size,
                },
                "sync": self._sync_payload(),
            },
        )

    def _disconnect_yandex(self) -> None:
        self._drain_body()
        with self.server.sync_lock:
            self._save_settings_fields(
                yandex_token="",
                yandex_connected=False,
                yandex_total_space=0,
                yandex_used_space=0,
                yandex_trash_size=0,
                yandex_last_check="",
            )
            self.server.yandex_token = ""
            self.server.yandex_connected = False
            self.server.yandex_total_space = 0
            self.server.yandex_used_space = 0
            self.server.yandex_trash_size = 0
            self.server.yandex_last_check = ""
        return self.send_json(200, {"sync": self._sync_payload()})

    def _require_yandex_token(self) -> str:
        if not self.server.yandex_token:
            raise ApiError(401, "Сначала подключите Яндекс Диск")
        return self.server.yandex_token

    def _yandex_send(self) -> None:
        try:
            with self.server.sync_lock:
                token = self._require_yandex_token()
                result = yandex.send(
                    self.server.db, token, self.server.device_name
                )
                self._remember_sync(result.get("manifest") or {})
        except yandex.AuthRequired as exc:
            raise ApiError(401, str(exc)) from exc
        except yandex.YandexError as exc:
            raise ApiError(502, f"Яндекс Диск: {exc}") from exc
        return self.send_json(
            200,
            {
                "ok": True,
                "provider": "yandex",
                "manifest": result.get("manifest"),
                "sync": self._sync_payload(),
            },
        )

    def _yandex_receive(self) -> None:
        try:
            with self.server.sync_lock:
                token = self._require_yandex_token()
                result = yandex.receive(self.server.db, token)
                self._remember_sync(result.get("manifest") or {})
        except yandex.AuthRequired as exc:
            raise ApiError(401, str(exc)) from exc
        except yandex.YandexError as exc:
            raise ApiError(502, f"Яндекс Диск: {exc}") from exc
        return self.send_json(
            200,
            {
                "ok": True,
                "provider": "yandex",
                "manifest": result.get("manifest"),
                "info": result.get("info"),
                "sync": self._sync_payload(),
            },
        )

    def _remember_sync(self, manifest: dict) -> None:
        self.server.last_sync = {
            "version": manifest.get("version"),
            "device": manifest.get("device"),
            "at": manifest.get("updated_at"),
            "sha256": manifest.get("sha256"),
        }
        stored = app_settings.load(self.server.settings_path)
        stored["sync_folder"] = self.server.sync_folder
        stored["sync_mode"] = self.server.sync_mode
        stored["device_name"] = self.server.device_name
        stored["last_sync"] = self.server.last_sync
        stored["drive_folder_id"] = self.server.drive_folder_id
        stored["drive_files_id"] = self.server.drive_files_id
        app_settings.save(stored, self.server.settings_path)

    def _browse_database(self) -> None:
        self._drain_body()
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
        filename = f"tasker-{stamp}.zip"
        with tempfile.TemporaryDirectory(prefix="tasker-archive-") as tmp:
            archive = Path(tmp) / "tasker.zip"
            self.server.db.backup_archive_to(archive)
            size = archive.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", _disposition("attachment", filename))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                with archive.open("rb") as handle:
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

    def _send_html(self, title: str, message: str) -> None:
        body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; background:#f4f2eb; color:#211f1a;
display:grid; place-items:center; min-height:100vh; margin:0; }}
main {{ max-width:520px; padding:28px; background:#fff; border:1px solid #e6e2d7;
border-radius:14px; box-shadow:0 8px 30px rgba(0,0,0,.08); }}
h1 {{ font-size:20px; margin:0 0 10px; }}
p {{ margin:0; color:#4b473e; line-height:1.6; }}
</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></main></body></html>"""
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

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

    def _drain_body(self) -> None:
        """Прочитать тело POST, которое обработчику не нужно."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self.close_connection = True
            return
        if length <= 0:
            return
        if length > MAX_JSON_BODY:
            self.close_connection = True
            return
        try:
            self.rfile.read(length)
        except OSError:
            self.close_connection = True

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
