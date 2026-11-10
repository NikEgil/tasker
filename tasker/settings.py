"""Пользовательские настройки Tasker (путь к базе и т. п.)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CALENDAR = {
    "show_project_name": True,
    "show_completed": True,
    "sort_projects_by_tasks": False,
    "show_weekends": True,
}


def normalize_calendar(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    result = {}
    for key, default in DEFAULT_CALENDAR.items():
        candidate = source.get(key)
        result[key] = candidate if isinstance(candidate, bool) else default
    return result


def config_dir() -> Path:
    override = os.environ.get("TASKER_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Tasker"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tasker"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = Path(path) if path else settings_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any], path: str | os.PathLike[str] | None = None) -> Path:
    target = Path(path) if path else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=".settings-", suffix=".json", dir=str(target.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return target
