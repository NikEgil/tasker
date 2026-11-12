"""Обмен текстовой базой и папкой вложений через локальную папку Google Диска.

Первый этап синхронизации: пользователь сам нажимает «Отправить» или
«Скачать». Никакого автоматического слияния и фоновых изменений нет.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database, SCHEMA_VERSION, ValidationError

MANIFEST_NAME = "sync-meta.json"
DB_NAME = "tasker.db"
FILES_NAME = "tasker_files"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(folder: Path) -> Path:
    return folder / MANIFEST_NAME


def read_manifest(folder: str | os.PathLike[str]) -> dict[str, Any] | None:
    target = Path(folder).expanduser().resolve()
    path = _manifest_path(target)
    if not path.is_file():
        if (target / DB_NAME).is_file():
            # Совместимость с папкой, куда уже вручную положили базу.
            return {
                "format": 1,
                "version": 0,
                "device": "—",
                "updated_at": None,
                "db": DB_NAME,
                "files": FILES_NAME,
                "sha256": None,
                "size": (target / DB_NAME).stat().st_size,
                "schema": None,
            }
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Не удалось прочитать метаданные на Диске: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("Некорректные метаданные на Диске")
    return data


def _safe_child(folder: Path, name: Any) -> Path:
    value = str(name or "").strip()
    if not value or "/" in value or "\\" in value or value in (".", ".."):
        raise ValidationError("Некорректное имя файла в метаданных Диска")
    target = (folder / value).resolve()
    try:
        target.relative_to(folder)
    except ValueError:
        raise ValidationError("Некорректный путь в метаданных Диска") from None
    return target


def status(folder: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not folder:
        return {"folder": None, "exists": False, "remote": None}
    target = Path(folder).expanduser().resolve()
    if not target.is_dir():
        return {"folder": str(target), "exists": False, "remote": None}
    try:
        manifest = read_manifest(target)
    except (ValidationError, OSError) as exc:
        return {
            "folder": str(target),
            "exists": True,
            "remote": None,
            "error": str(exc),
        }
    return {"folder": str(target), "exists": True, "remote": manifest}


def send(
    db: Database,
    folder: str | os.PathLike[str],
    device_name: str,
) -> dict[str, Any]:
    target = Path(folder).expanduser().resolve()
    if target == db.data_dir or db.path.parent == target:
        raise ValidationError("Нельзя синхронизировать базу в её собственную папку")
    target.mkdir(parents=True, exist_ok=True)
    existing = read_manifest(target)
    version = int(existing.get("version", 0) or 0) + 1 if existing else 1

    staging = Path(tempfile.mkdtemp(prefix=".tasker-sync-", dir=str(target)))
    old_db = target / DB_NAME
    old_files = target / FILES_NAME
    previous = target / f".previous-{uuid.uuid4().hex}"
    try:
        staged_db = staging / DB_NAME
        db.backup_to(staged_db)
        staged_files = staging / FILES_NAME
        db.copy_attachment_files(staged_files)
        checksum = _sha256(staged_db)
        manifest = {
            "format": 1,
            "version": version,
            "device": device_name,
            "updated_at": _now(),
            "db": DB_NAME,
            "files": FILES_NAME,
            "sha256": checksum,
            "size": staged_db.stat().st_size,
            "schema": SCHEMA_VERSION,
            "attachments": sum(1 for _ in staged_files.rglob("*") if _.is_file()),
        }
        previous.mkdir(parents=True, exist_ok=True)
        if old_db.exists():
            os.replace(old_db, previous / DB_NAME)
        if old_files.exists():
            os.replace(old_files, previous / FILES_NAME)
        os.replace(staged_db, old_db)
        os.replace(staged_files, old_files)
        temporary_manifest = staging / MANIFEST_NAME
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_manifest, _manifest_path(target))
        shutil.rmtree(previous, ignore_errors=True)
        return {"ok": True, "manifest": manifest, "folder": str(target)}
    except BaseException:
        # Вернуть предыдущую версию, если замена успела начаться.
        if previous.exists():
            if old_db.exists():
                old_db.unlink(missing_ok=True)
            if old_files.exists():
                shutil.rmtree(old_files, ignore_errors=True)
            if (previous / DB_NAME).exists():
                os.replace(previous / DB_NAME, old_db)
            if (previous / FILES_NAME).exists():
                os.replace(previous / FILES_NAME, old_files)
            shutil.rmtree(previous, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def receive(db: Database, folder: str | os.PathLike[str]) -> dict[str, Any]:
    target = Path(folder).expanduser().resolve()
    manifest = read_manifest(target)
    if manifest is None:
        raise ValidationError("В выбранной папке нет данных Tasker")
    db_path = _safe_child(target, manifest.get("db", DB_NAME))
    files_path = _safe_child(target, manifest.get("files", FILES_NAME))
    if not db_path.is_file():
        raise ValidationError("На Диске не найден файл базы")
    expected = manifest.get("sha256")
    if expected and _sha256(db_path) != expected:
        raise ValidationError("Контрольная сумма базы на Диске не совпадает")
    if not files_path.is_dir():
        raise ValidationError("На Диске не найдена папка файлов")
    info = db.replace_from_snapshot(db_path, files_path)
    return {
        "ok": True,
        "manifest": manifest,
        "folder": str(target),
        "info": info,
    }
