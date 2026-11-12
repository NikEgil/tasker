"""Прямая работа с Google Drive через REST API, без Google Drive для компьютера.

OAuth-клиент создаётся пользователем в Google Cloud Console. Приложение
использует loopback-redirect и scope drive.file: доступ только к файлам,
созданным самим Tasker.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import secrets
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database, SCHEMA_VERSION, ValidationError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
MANIFEST_NAME = "sync-meta.json"
DB_NAME = "tasker.db"
FILES_NAME = "tasker_files"


class DriveError(Exception):
    """Ошибка Google Drive."""


class AuthRequired(DriveError):
    """Требуется повторная авторизация."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_verifier() -> str:
    return secrets.token_urlsafe(64)


def make_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def auth_url(client_id: str, redirect_uri: str, state: str, verifier: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": make_challenge(verifier),
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, values: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(values).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {}
        raise DriveError(detail.get("error_description") or str(exc)) from exc
    except OSError as exc:
        raise DriveError(f"Google недоступен: {exc}") from exc


def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> dict[str, Any]:
    values = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        values["client_secret"] = client_secret
    return _post_form(TOKEN_URL, values)


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str
) -> dict[str, Any]:
    values = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client_secret:
        values["client_secret"] = client_secret
    return _post_form(TOKEN_URL, values)


class DriveClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    def _request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> urllib.response.addinfourl:
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                **(headers or {}),
            },
            method=method,
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise AuthRequired("Нужно заново подключить Google Диск") from exc
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = detail.get("error", {}).get("message") or str(exc)
            except Exception:
                message = str(exc)
            raise DriveError(message) from exc
        except OSError as exc:
            raise DriveError(f"Google Drive недоступен: {exc}") from exc

    def _json(self, method: str, path: str, payload: Any = None, params: dict | None = None):
        url = f"{DRIVE_API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        with self._request(method, url, data=data, headers=headers) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}

    def list_children(self, folder_id: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page_token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "pageSize": "1000",
                "fields": "nextPageToken,files(id,name,mimeType,size,appProperties)",
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._json("GET", "/files", params=params)
            files.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return files

    def find_child(
        self, parent_id: str, name: str, mime: str | None = None
    ) -> dict[str, Any] | None:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"'{parent_id}' in parents and name = '{escaped}' and trashed = false"
        if mime:
            query += f" and mimeType = '{mime}'"
        data = self._json(
            "GET",
            "/files",
            params={
                "q": query,
                "pageSize": "10",
                "fields": "files(id,name,mimeType,size,appProperties)",
            },
        )
        files = data.get("files", [])
        return files[0] if files else None

    def create_folder(self, parent_id: str, name: str) -> dict[str, Any]:
        return self._json(
            "POST",
            "/files",
            {
                "name": name,
                "mimeType": FOLDER_MIME,
                "parents": [parent_id],
            },
            params={"fields": "id,name,mimeType"},
        )

    def ensure_folder(self, parent_id: str, name: str) -> dict[str, Any]:
        existing = self.find_child(parent_id, name, FOLDER_MIME)
        return existing or self.create_folder(parent_id, name)

    def upload(
        self,
        parent_id: str,
        name: str,
        content: bytes,
        mime: str,
        *,
        existing_id: str | None = None,
        app_properties: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"name": name}
        if existing_id is None:
            metadata["parents"] = [parent_id]
        if app_properties:
            metadata["appProperties"] = app_properties
        action = "PATCH" if existing_id else "POST"
        suffix = f"/{existing_id}" if existing_id else ""
        url = f"{DRIVE_UPLOAD}{suffix}?uploadType=resumable&fields=id,name,size,appProperties"
        body = json.dumps(metadata).encode("utf-8")
        with self._request(
            action,
            url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime,
                "X-Upload-Content-Length": str(len(content)),
            },
        ) as response:
            location = response.headers.get("Location")
        if not location:
            raise DriveError("Google Drive не выдал адрес для загрузки")
        with self._request(
            "PUT",
            location,
            data=content,
            headers={
                "Content-Type": mime,
                "Content-Length": str(len(content)),
            },
            timeout=300,
        ) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}

    def download(self, file_id: str) -> bytes:
        with self._request(
            "GET",
            f"{DRIVE_API}/files/{file_id}?alt=media",
            headers={"Accept": "*/*"},
        ) as response:
            return response.read()

    def delete(self, file_id: str) -> None:
        with self._request("DELETE", f"{DRIVE_API}/files/{file_id}") as response:
            response.read()


def _db_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_folders(
    client: DriveClient, folder_id: str | None, files_id: str | None
) -> tuple[str, str]:
    if not folder_id:
        folder = client.ensure_folder("root", "Tasker")
        folder_id = folder["id"]
    if not files_id:
        files = client.ensure_folder(folder_id, FILES_NAME)
        files_id = files["id"]
    return folder_id, files_id


def _manifest_from_remote(client: DriveClient, folder_id: str) -> dict[str, Any] | None:
    child = client.find_child(folder_id, MANIFEST_NAME)
    if not child:
        return None
    raw = client.download(child["id"])
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriveError("Некорректные метаданные на Google Диске") from exc
    return data if isinstance(data, dict) else None


def send(
    db: Database,
    client: DriveClient,
    folder_id: str | None,
    files_id: str | None,
    device_name: str,
) -> dict[str, Any]:
    folder_id, files_id = ensure_folders(client, folder_id, files_id)
    remote_manifest = _manifest_from_remote(client, folder_id)
    version = int((remote_manifest or {}).get("version", 0)) + 1

    with tempfile.TemporaryDirectory(prefix="tasker-drive-") as tmp:
        snapshot = Path(tmp) / DB_NAME
        db.backup_to(snapshot)
        local_files: dict[str, Path] = {}
        for path in db.attachment_files():
            local_files[path.name] = path
        remote_files = {
            item["name"]: item
            for item in client.list_children(files_id)
            if item.get("mimeType") != FOLDER_MIME
        }
        for name, path in local_files.items():
            checksum = _file_sha(path)
            existing = remote_files.get(name)
            app_properties = {"sha256": checksum}
            if existing and existing.get("appProperties", {}).get("sha256") == checksum:
                continue
            client.upload(
                files_id,
                name,
                path.read_bytes(),
                mimetypes.guess_type(name)[0] or "application/octet-stream",
                existing_id=existing["id"] if existing else None,
                app_properties=app_properties,
            )
        for name, item in remote_files.items():
            if name not in local_files:
                client.delete(item["id"])

        db_checksum = _db_sha(snapshot)
        db_file = client.find_child(folder_id, DB_NAME)
        client.upload(
            folder_id,
            DB_NAME,
            snapshot.read_bytes(),
            "application/x-sqlite3",
            existing_id=db_file["id"] if db_file else None,
            app_properties={"sha256": db_checksum},
        )
        manifest = {
            "format": 1,
            "version": version,
            "device": device_name,
            "updated_at": _now(),
            "sha256": db_checksum,
            "size": snapshot.stat().st_size,
            "schema": SCHEMA_VERSION,
            "attachments": len(local_files),
            "folder_id": folder_id,
            "files_id": files_id,
        }
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        manifest_file = client.find_child(folder_id, MANIFEST_NAME)
        client.upload(
            folder_id,
            MANIFEST_NAME,
            encoded,
            "application/json",
            existing_id=manifest_file["id"] if manifest_file else None,
        )
        return {
            "ok": True,
            "manifest": manifest,
            "folder_id": folder_id,
            "files_id": files_id,
        }


def receive(
    db: Database, client: DriveClient, folder_id: str | None
) -> dict[str, Any]:
    if not folder_id:
        raise DriveError("Папка Tasker на Google Диске не найдена")
    manifest = _manifest_from_remote(client, folder_id)
    if not manifest:
        raise DriveError("В папке Tasker нет данных для скачивания")
    files_id = manifest.get("files_id")
    if not files_id:
        files_folder = client.find_child(folder_id, FILES_NAME, FOLDER_MIME)
        files_id = files_folder["id"] if files_folder else None
    if not files_id:
        raise DriveError("На Google Диске нет папки файлов")
    db_file = client.find_child(folder_id, DB_NAME)
    if not db_file:
        raise DriveError("На Google Диске нет файла базы")
    db_bytes = client.download(db_file["id"])
    expected = manifest.get("sha256")
    if expected and hashlib.sha256(db_bytes).hexdigest() != expected:
        raise DriveError("Контрольная сумма базы на Google Диске не совпадает")
    remote_files = {
        item["name"]: item
        for item in client.list_children(files_id)
        if item.get("mimeType") != FOLDER_MIME
    }
    with tempfile.TemporaryDirectory(prefix="tasker-drive-") as tmp:
        root = Path(tmp)
        db_path = root / DB_NAME
        db_path.write_bytes(db_bytes)
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT storage_path FROM attachments").fetchall()
        finally:
            conn.close()
        files_root = root / FILES_NAME
        for (storage_path,) in rows:
            relative = Path(storage_path)
            remote = remote_files.get(relative.name)
            if not remote:
                raise DriveError(f"На Google Диске не хватает файла {relative.name}")
            target = files_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(client.download(remote["id"]))
        info = db.replace_from_snapshot(db_path, files_root)
    return {"ok": True, "manifest": manifest, "info": info}


def find_root(client: DriveClient) -> dict[str, Any] | None:
    return client.find_child("root", "Tasker", FOLDER_MIME)


def remote_manifest(
    client: DriveClient, folder_id: str | None = None
) -> dict[str, Any] | None:
    if not folder_id:
        root = find_root(client)
        folder_id = root["id"] if root else None
    if not folder_id:
        return None
    return _manifest_from_remote(client, folder_id)
