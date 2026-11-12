"""Прямая работа с Яндекс Диском через REST API.

Используется папка приложения (`app:/`), поэтому токену достаточно права
`cloud_api:disk.app_folder`. Авторизация — OAuth-токен Яндекса, который
пользователь вставляет в настройках.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database, SCHEMA_VERSION

API = "https://cloud-api.yandex.net/v1/disk"
MANIFEST_PATH = "app:/sync-meta.json"
DB_PATH = "app:/tasker.db"
FILES_PATH = "app:/tasker_files"
MANIFEST_NAME = "sync-meta.json"


class YandexError(Exception):
    """Ошибка Яндекс Диска."""


class AuthRequired(YandexError):
    """Токен недействителен или просрочен."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class YandexClient:
    def __init__(self, token: str):
        self.token = token

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 90,
        allow_404: bool = False,
    ) -> bytes:
        url = path if path.startswith(("http://", "https://")) else f"{API}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"OAuth {self.token}",
                "Accept": "application/json",
                **(headers or {}),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_404:
                return b""
            if exc.code == 401:
                raise AuthRequired(
                    "Токен Яндекс Диска недействителен или не имеет доступа к папке приложения"
                ) from exc
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = detail.get("description") or detail.get("message") or str(exc)
            except Exception:
                message = str(exc)
            raise YandexError(message) from exc
        except OSError as exc:
            raise YandexError(f"Яндекс Диск недоступен: {exc}") from exc

    def _json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: Any = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        raw = self._request(
            method,
            path,
            params=params,
            data=data,
            headers=headers,
            allow_404=allow_404,
        )
        if not raw:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise YandexError("Яндекс Диск вернул некорректный ответ") from exc
        return value if isinstance(value, dict) else None

    def disk_info(self) -> dict[str, Any]:
        return self._json("GET", "") or {}

    def resource(self, path: str) -> dict[str, Any] | None:
        return self._json("GET", "resources", params={"path": path}, allow_404=True)

    def ensure_folder(self, path: str) -> None:
        existing = self.resource(path)
        if existing:
            if existing.get("type") != "dir":
                raise YandexError(f"На Яндекс Диске {path} — не папка")
            return
        self._json("PUT", "resources", params={"path": path})
        if not self.resource(path):
            raise YandexError(f"Не удалось создать папку {path}")

    def list_files(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._json(
                "GET",
                "resources",
                params={"path": path, "limit": 1000, "offset": offset},
                allow_404=True,
            )
            if not data:
                return items
            embedded = data.get("_embedded") or {}
            page = embedded.get("items") or []
            items.extend(page)
            total = int(embedded.get("total") or data.get("total") or len(items))
            offset += len(page)
            if not page or offset >= total:
                return items

    def upload(self, path: str, content: bytes, filename: str) -> None:
        link = self._json(
            "GET",
            "resources/upload",
            params={"path": path, "overwrite": "true"},
        )
        href = (link or {}).get("href")
        if not href:
            raise YandexError("Яндекс Диск не выдал URL для загрузки")
        method = str((link or {}).get("method") or "PUT").upper()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._request(
            method,
            href,
            data=content,
            headers={"Content-Type": mime},
            timeout=300,
        )

    def download(self, path: str) -> bytes:
        link = self._json("GET", "resources/download", params={"path": path})
        href = (link or {}).get("href")
        if not href:
            raise YandexError("Яндекс Диск не выдал URL для скачивания")
        return self._request("GET", href, headers={"Accept": "*/*"}, timeout=300)

    def delete(self, path: str) -> None:
        self._json(
            "DELETE",
            "resources",
            params={"path": path, "permanently": "true"},
            allow_404=True,
        )


def status(token: str) -> dict[str, Any]:
    client = YandexClient(token)
    info = client.disk_info()
    return {
        "connected": True,
        "total_space": int(info.get("total_space") or 0),
        "used_space": int(info.get("used_space") or 0),
        "trash_size": int(info.get("trash_size") or 0),
    }


def remote_manifest(token: str) -> dict[str, Any] | None:
    return _manifest(YandexClient(token))


def _manifest(client: YandexClient) -> dict[str, Any] | None:
    resource = client.resource(MANIFEST_PATH)
    if not resource or resource.get("type") != "file":
        return None
    raw = client.download(MANIFEST_PATH)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YandexError("Некорректные метаданные на Яндекс Диске") from exc
    return data if isinstance(data, dict) else None


def send(db: Database, token: str, device_name: str) -> dict[str, Any]:
    client = YandexClient(token)
    client.ensure_folder(FILES_PATH)
    remote_manifest = _manifest(client)
    version = int((remote_manifest or {}).get("version", 0)) + 1

    with tempfile.TemporaryDirectory(prefix="tasker-yandex-") as tmp:
        snapshot = Path(tmp) / "tasker.db"
        db.backup_to(snapshot)
        local_files = {path.name: path for path in db.attachment_files()}
        remote_files = {
            item["name"]: item
            for item in client.list_files(FILES_PATH)
            if item.get("type") != "dir"
        }
        for name, path in local_files.items():
            checksum = _md5(path)
            existing = remote_files.get(name)
            if existing and str(existing.get("md5") or "").lower() == checksum:
                continue
            client.upload(f"{FILES_PATH}/{name}", path.read_bytes(), name)
        for name in remote_files:
            if name not in local_files:
                client.delete(f"{FILES_PATH}/{name}")

        content = snapshot.read_bytes()
        checksum = _sha256(content)
        client.upload(DB_PATH, content, "tasker.db")
        manifest = {
            "format": 1,
            "provider": "yandex",
            "version": version,
            "device": device_name,
            "updated_at": _now(),
            "sha256": checksum,
            "size": len(content),
            "schema": SCHEMA_VERSION,
            "attachments": len(local_files),
        }
        client.upload(
            MANIFEST_PATH,
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            MANIFEST_NAME,
        )
        return {"ok": True, "manifest": manifest}


def receive(db: Database, token: str) -> dict[str, Any]:
    client = YandexClient(token)
    manifest = _manifest(client)
    if not manifest:
        raise YandexError("В папке приложения на Яндекс Диске нет данных для скачивания")
    db_bytes = client.download(DB_PATH)
    expected = manifest.get("sha256")
    if expected and _sha256(db_bytes) != expected:
        raise YandexError("Контрольная сумма базы на Яндекс Диске не совпадает")
    remote_files = {
        item["name"]: item
        for item in client.list_files(FILES_PATH)
        if item.get("type") != "dir"
    }
    with tempfile.TemporaryDirectory(prefix="tasker-yandex-") as tmp:
        root = Path(tmp)
        db_path = root / "tasker.db"
        db_path.write_bytes(db_bytes)
        conn = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT storage_path FROM attachments").fetchall()
        finally:
            conn.close()
        files_root = root / "tasker_files"
        for (storage_path,) in rows:
            relative = Path(storage_path)
            if relative.name not in remote_files:
                raise YandexError(f"На Яндекс Диске не хватает файла {relative.name}")
            target = files_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(client.download(f"{FILES_PATH}/{relative.name}"))
        info = db.replace_from_snapshot(db_path, files_root)
    return {"ok": True, "manifest": manifest, "info": info}
