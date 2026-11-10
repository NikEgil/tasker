"""SQLite-хранилище Tasker.

Вся информация приложения — проекты, задачи, заметки и вложения — лежит
в одном файле базы данных. Это делает файл самодостаточным: его можно
скопировать, забрать на другой компьютер или синхронизировать облаком.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2

STATUSES = ("urgent", "later", "done")
STATUS_LABELS = {"urgent": "Срочно", "later": "Потом", "done": "Сделано"}

PROJECT_COLORS = [
    "#4c6ef5",
    "#7048e8",
    "#0c8599",
    "#2f9e44",
    "#e8590c",
    "#c2255c",
    "#5c7cfa",
    "#495057",
]

MAX_PROJECT_NAME = 120
MAX_TITLE = 300
MAX_DESCRIPTION = 20_000
MAX_MESSAGE = 20_000
MAX_FILES = 12
MAX_FILE_SIZE = 40 * 1024 * 1024
MAX_FILES_TOTAL = 96 * 1024 * 1024

IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
}

UNSET = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    color      TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'urgent'
                CHECK (status IN ('urgent', 'later', 'done')),
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    done_at     TEXT,
    scheduled_date TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id         TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    mime       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    data       BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_status
    ON tasks(project_id, status, position);
CREATE INDEX IF NOT EXISTS idx_messages_project_created
    ON messages(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attachments_message
    ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_project
    ON attachments(project_id);
"""


class DatabaseError(Exception):
    """Базовая ошибка хранилища."""


class NotFoundError(DatabaseError):
    """Запись не найдена."""


class ValidationError(DatabaseError):
    """Некорректные входные данные."""


def now_iso() -> str:
    """Текущее время в UTC в формате ISO 8601 с миллисекундами."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_id() -> str:
    return uuid.uuid4().hex


def _clean_title(value: Any, field: str, max_len: int) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field}: ожидается строка")
    value = " ".join(value.split())
    if not value:
        raise ValidationError(f"{field} не может быть пустым")
    if len(value) > max_len:
        raise ValidationError(f"{field}: не больше {max_len} символов")
    return value


def _clean_text(value: Any, field: str, max_len: int) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field}: ожидается строка")
    value = value.strip()
    if len(value) > max_len:
        raise ValidationError(f"{field}: не больше {max_len} символов")
    return value


def _clean_status(value: Any) -> str:
    if value not in STATUSES:
        raise ValidationError("Неизвестная колонка задачи")
    return str(value)


def _clean_color(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValidationError("Некорректный цвет проекта")
    return value.lower()


def _clean_scheduled_date(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Некорректная дата задачи")
    value = value.strip()
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationError("Некорректная дата задачи")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValidationError("Некорректная дата задачи") from None
    return value


def clean_filename(value: Any) -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    if not name:
        name = "файл"
    return name[:180]


class Database:
    """SQLite-база Tasker с одним долгоживущим соединением.

    Соединение открывается на время работы приложения и закрывается при
    штатном завершении. Все операции сериализуются внутренним RLock, а
    каждая запись подтверждается через COMMIT с synchronous=FULL — поэтому
    перезапуск или аварийное закрытие не теряет уже сохранённые данные
    (незавершённая транзакция откатывается самим SQLite).
    """

    def __init__(self, path: str | os.PathLike[str]):
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._revision = 0
        self._last_write_at: str | None = None
        target = Path(path).expanduser().resolve()
        self.inspect_path(target)
        conn = self._open_connection(target)
        try:
            self._init_schema(conn)
        except BaseException:
            conn.close()
            raise
        self.path = target
        self.data_dir = target.parent
        self.backup_dir = self.data_dir / "backups"
        self._conn = conn

    # ------------------------------------------------------------------
    # соединение и схема
    # ------------------------------------------------------------------
    @staticmethod
    def _open_connection(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path),
            timeout=20,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 20000")
        # DELETE-journal оставляет после завершения транзакции только
        # один файл .db — это важно для синхронизации.
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

        # Мягкая миграция: старые базы получают новые поля без перезаписи.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "scheduled_date" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN scheduled_date TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_date)"
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError("База данных закрыта")
        return self._conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._connection()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
                self._revision += 1
                self._last_write_at = now_iso()
            except BaseException:
                conn.rollback()
                raise

    def close(self) -> None:
        """Закрыть соединение (идемпотентно)."""
        with self._lock:
            conn = self._conn
            self._conn = None
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

    # ------------------------------------------------------------------
    # проверка и переключение файла базы
    # ------------------------------------------------------------------
    @staticmethod
    def inspect_path(path: str | os.PathLike[str]) -> dict[str, Any]:
        """Проверить, что по пути лежит либо новый, либо Tasker-файл."""
        target = Path(path).expanduser().resolve()
        if target.exists() and target.is_dir():
            raise ValidationError("Выбран путь к папке, а не к файлу базы")
        if not target.exists():
            return {"exists": False, "valid": True, "tables": []}
        size = target.stat().st_size
        if size == 0:
            return {"exists": True, "valid": True, "empty": True, "tables": []}
        try:
            with target.open("rb") as handle:
                header = handle.read(16)
        except OSError as exc:
            raise ValidationError(f"Не удалось прочитать файл: {exc}") from exc
        if header != b"SQLite format 3\x00":
            raise ValidationError("Файл не является базой данных SQLite")
        try:
            conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                required = {
                    "projects": {"id", "name", "color", "position", "created_at"},
                    "tasks": {
                        "id",
                        "project_id",
                        "title",
                        "status",
                        "position",
                        "created_at",
                        "updated_at",
                    },
                }
                if tables and not set(required).issubset(tables):
                    raise ValidationError("Этот файл не похож на базу Tasker")
                if tables:
                    for table, columns in required.items():
                        actual = {
                            str(row[1])
                            for row in conn.execute(f"PRAGMA table_info({table})")
                        }
                        if not columns.issubset(actual):
                            raise ValidationError("Этот файл не похож на базу Tasker")
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            raise ValidationError(f"Не удалось открыть базу SQLite: {exc}") from exc
        return {"exists": True, "valid": True, "tables": sorted(tables)}

    def switch_path(self, new_path: str | os.PathLike[str]) -> dict[str, Any]:
        """Переключиться на другую базу, не трогая текущий файл."""
        target = Path(new_path).expanduser().resolve()
        with self._lock:
            if target == self.path:
                return self.info()
            self.inspect_path(target)
            new_conn = self._open_connection(target)
            try:
                self._init_schema(new_conn)
            except BaseException:
                new_conn.close()
                raise
            old_conn = self._conn
            self.path = target
            self.data_dir = target.parent
            self.backup_dir = self.data_dir / "backups"
            self._conn = new_conn
            self._revision = 0
            self._last_write_at = None
            if old_conn is not None:
                old_conn.close()
            return self.info()

    # ------------------------------------------------------------------
    # проекты
    # ------------------------------------------------------------------
    def _project_row(self, conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Проект не найден")
        return row

    def _task_counts(self, conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
        counts = {"urgent": 0, "later": 0, "done": 0}
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks WHERE project_id = ? GROUP BY status",
            (project_id,),
        ).fetchall()
        for row in rows:
            if row["status"] in counts:
                counts[row["status"]] = int(row["n"])
        return counts

    def _message_count(self, conn: sqlite3.Connection, project_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE project_id = ?", (project_id,)
        ).fetchone()
        return int(row["n"])

    def _project_dict(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "color": row["color"],
            "position": row["position"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "counts": self._task_counts(conn, row["id"]),
            "messages": self._message_count(conn, row["id"]),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY position, created_at"
            ).fetchall()
            return [self._project_dict(conn, row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.read() as conn:
            row = self._project_row(conn, project_id)
            return self._project_dict(conn, row)

    def reorder_projects(self, project_ids: Any) -> list[dict[str, Any]]:
        if not isinstance(project_ids, list):
            raise ValidationError("Некорректный порядок проектов")
        ordered: list[str] = []
        seen: set[str] = set()
        for value in project_ids:
            if not isinstance(value, str) or not value:
                raise ValidationError("Некорректный порядок проектов")
            if value in seen:
                raise ValidationError("Проекты в списке повторяются")
            seen.add(value)
            ordered.append(value)

        with self.write() as conn:
            existing = [
                row["id"] for row in conn.execute("SELECT id FROM projects").fetchall()
            ]
            if len(existing) != len(ordered) or set(existing) != seen:
                raise ValidationError("Нужно передать все проекты в новом порядке")
            stamp = now_iso()
            for index, project_id in enumerate(ordered):
                conn.execute(
                    "UPDATE projects SET position = ?, updated_at = ? WHERE id = ?",
                    (index, stamp, project_id),
                )
        return self.list_projects()

    def _pick_color(self, conn: sqlite3.Connection) -> str:
        used = {
            row["color"]: int(row["n"])
            for row in conn.execute(
                "SELECT color, COUNT(*) AS n FROM projects GROUP BY color"
            ).fetchall()
        }
        return min(PROJECT_COLORS, key=lambda color: (used.get(color, 0), PROJECT_COLORS.index(color)))

    def create_project(
        self, name: Any, color: Any = None
    ) -> dict[str, Any]:
        clean_name = _clean_title(name, "Название проекта", MAX_PROJECT_NAME)
        with self.write() as conn:
            if color is None:
                clean_color = self._pick_color(conn)
            else:
                clean_color = _clean_color(color)
            position_row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM projects"
            ).fetchone()
            position = int(position_row["p"])
            project_id = new_id()
            stamp = now_iso()
            conn.execute(
                "INSERT INTO projects(id, name, color, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, clean_name, clean_color, position, stamp, stamp),
            )
            row = self._project_row(conn, project_id)
            return self._project_dict(conn, row)

    def update_project(
        self,
        project_id: str,
        name: Any = UNSET,
        color: Any = UNSET,
    ) -> dict[str, Any]:
        with self.write() as conn:
            row = self._project_row(conn, project_id)
            new_name = row["name"] if name is UNSET else _clean_title(
                name, "Название проекта", MAX_PROJECT_NAME
            )
            new_color = row["color"] if color is UNSET else _clean_color(color)
            conn.execute(
                "UPDATE projects SET name = ?, color = ?, updated_at = ? WHERE id = ?",
                (new_name, new_color, now_iso(), project_id),
            )
            row = self._project_row(conn, project_id)
            return self._project_dict(conn, row)

    def delete_project(self, project_id: str) -> None:
        with self.write() as conn:
            self._project_row(conn, project_id)
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ------------------------------------------------------------------
    # задачи
    # ------------------------------------------------------------------
    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "position": row["position"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "done_at": row["done_at"],
            "scheduled_date": row["scheduled_date"],
        }

    def _task_row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError("Задача не найдена")
        return row

    def list_tasks(self, project_id: str) -> list[dict[str, Any]]:
        with self.read() as conn:
            self._project_row(conn, project_id)
            rows = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? "
                "ORDER BY CASE status WHEN 'urgent' THEN 0 WHEN 'later' THEN 1 ELSE 2 END, "
                "position, created_at",
                (project_id,),
            ).fetchall()
            return [self._task_dict(row) for row in rows]

    def list_calendar(self, start: str, end: str) -> dict[str, Any]:
        """Задачи в диапазоне дат и все задачи без даты."""
        with self.read() as conn:
            scheduled = conn.execute(
                "SELECT t.*, p.name AS project_name, p.color AS project_color "
                "FROM tasks t JOIN projects p ON p.id = t.project_id "
                "WHERE t.scheduled_date IS NOT NULL "
                "AND t.scheduled_date >= ? AND t.scheduled_date <= ? "
                "ORDER BY t.scheduled_date, "
                "CASE t.status WHEN 'urgent' THEN 0 WHEN 'later' THEN 1 ELSE 2 END, "
                "t.position, t.created_at",
                (start, end),
            ).fetchall()
            unscheduled = conn.execute(
                "SELECT t.*, p.name AS project_name, p.color AS project_color "
                "FROM tasks t JOIN projects p ON p.id = t.project_id "
                "WHERE t.scheduled_date IS NULL "
                "ORDER BY p.position, p.created_at, "
                "CASE t.status WHEN 'urgent' THEN 0 WHEN 'later' THEN 1 ELSE 2 END, "
                "t.position, t.created_at",
            ).fetchall()

        def serialize(row: sqlite3.Row) -> dict[str, Any]:
            data = self._task_dict(row)
            data["project_name"] = row["project_name"]
            data["project_color"] = row["project_color"]
            return data

        return {
            "start": start,
            "end": end,
            "scheduled": [serialize(row) for row in scheduled],
            "unscheduled": [serialize(row) for row in unscheduled],
        }

    def create_task(
        self,
        project_id: str,
        title: Any,
        description: Any = "",
        status: Any = "urgent",
        scheduled_date: Any = None,
    ) -> dict[str, Any]:
        clean_title = _clean_title(title, "Название задачи", MAX_TITLE)
        clean_description = _clean_text(description, "Описание", MAX_DESCRIPTION)
        clean_status = _clean_status(status)
        clean_date = _clean_scheduled_date(scheduled_date)
        with self.write() as conn:
            self._project_row(conn, project_id)
            position_row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM tasks "
                "WHERE project_id = ? AND status = ?",
                (project_id, clean_status),
            ).fetchone()
            position = int(position_row["p"])
            task_id = new_id()
            stamp = now_iso()
            conn.execute(
                "INSERT INTO tasks(id, project_id, title, description, status, position, "
                "created_at, updated_at, done_at, scheduled_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    project_id,
                    clean_title,
                    clean_description,
                    clean_status,
                    position,
                    stamp,
                    stamp,
                    stamp if clean_status == "done" else None,
                    clean_date,
                ),
            )
            task = self._task_dict(self._task_row(conn, task_id))
            project = self._project_dict(conn, self._project_row(conn, project_id))
            return {"task": task, "project": project}

    def _place_task(
        self,
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        status: str,
        index: int | None,
    ) -> None:
        """Переставить задачу внутрь колонки и нормализовать позиции."""
        siblings = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM tasks WHERE project_id = ? AND status = ? AND id != ? "
                "ORDER BY position, created_at",
                (task["project_id"], status, task["id"]),
            ).fetchall()
        ]
        if index is None or index < 0 or index > len(siblings):
            index = len(siblings)
        siblings.insert(int(index), task["id"])

        stamp = now_iso()
        done_at = task["done_at"]
        if status == "done":
            if task["status"] != "done" or not done_at:
                done_at = stamp
        else:
            done_at = None

        for position, sibling_id in enumerate(siblings):
            if sibling_id == task["id"]:
                conn.execute(
                    "UPDATE tasks SET status = ?, position = ?, updated_at = ?, done_at = ? "
                    "WHERE id = ?",
                    (status, position, stamp, done_at, sibling_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET position = ? WHERE id = ?", (position, sibling_id)
                )

    def update_task(
        self,
        task_id: str,
        title: Any = UNSET,
        description: Any = UNSET,
        status: Any = UNSET,
        scheduled_date: Any = UNSET,
    ) -> dict[str, Any]:
        with self.write() as conn:
            row = self._task_row(conn, task_id)
            new_title = row["title"] if title is UNSET else _clean_title(
                title, "Название задачи", MAX_TITLE
            )
            new_description = (
                row["description"]
                if description is UNSET
                else _clean_text(description, "Описание", MAX_DESCRIPTION)
            )
            new_status = row["status"] if status is UNSET else _clean_status(status)
            new_scheduled_date = (
                row["scheduled_date"]
                if scheduled_date is UNSET
                else _clean_scheduled_date(scheduled_date)
            )

            if new_status != row["status"]:
                self._place_task(conn, row, new_status, None)

            stamp = now_iso()
            done_at = row["done_at"]
            if new_status == "done" and not done_at:
                done_at = stamp
            if new_status != "done":
                done_at = None
            conn.execute(
                "UPDATE tasks SET title = ?, description = ?, done_at = ?, "
                "scheduled_date = ?, updated_at = ? WHERE id = ?",
                (
                    new_title,
                    new_description,
                    done_at,
                    new_scheduled_date,
                    stamp,
                    task_id,
                ),
            )
            task = self._task_dict(self._task_row(conn, task_id))
            project = self._project_dict(
                conn, self._project_row(conn, row["project_id"])
            )
            return {"task": task, "project": project}

    def move_task(self, task_id: str, status: Any, index: Any = None) -> dict[str, Any]:
        clean_status = _clean_status(status)
        try:
            clean_index = None if index is None else int(index)
        except (TypeError, ValueError):
            raise ValidationError("Некорректная позиция задачи") from None
        with self.write() as conn:
            row = self._task_row(conn, task_id)
            self._place_task(conn, row, clean_status, clean_index)
            task = self._task_dict(self._task_row(conn, task_id))
            project = self._project_dict(
                conn, self._project_row(conn, row["project_id"])
            )
            return {"task": task, "project": project}

    def delete_task(self, task_id: str) -> dict[str, Any]:
        with self.write() as conn:
            row = self._task_row(conn, task_id)
            project_id = row["project_id"]
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            project = self._project_dict(conn, self._project_row(conn, project_id))
            return {"ok": True, "project": project}

    # ------------------------------------------------------------------
    # заметки и вложения
    # ------------------------------------------------------------------
    @staticmethod
    def _attachment_meta(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        mime = str(data["mime"])
        return {
            "id": data["id"],
            "filename": data["filename"],
            "mime": mime,
            "size": int(data["size"]),
            "is_image": mime in IMAGE_MIME_TYPES,
            "url": f"/api/attachments/{data['id']}",
        }

    def _attachments_for(
        self, conn: sqlite3.Connection, message_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {mid: [] for mid in message_ids}
        if not message_ids:
            return grouped
        placeholders = ",".join("?" for _ in message_ids)
        rows = conn.execute(
            f"SELECT id, message_id, filename, mime, size FROM attachments "
            f"WHERE message_id IN ({placeholders}) ORDER BY rowid",
            message_ids,
        ).fetchall()
        for row in rows:
            grouped[row["message_id"]].append(self._attachment_meta(row))
        return grouped

    def _message_dict(
        self, row: sqlite3.Row, attachments: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "body": row["body"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "attachments": attachments,
        }

    def list_messages(
        self,
        project_id: str,
        limit: int = 100,
        before: str | None = None,
    ) -> dict[str, Any]:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 200))
        with self.read() as conn:
            project_row = self._project_row(conn, project_id)
            if before:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE project_id = ? AND created_at < ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (project_id, before, limit + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE project_id = ? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (project_id, limit + 1),
                ).fetchall()
            has_more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
            attachments = self._attachments_for(conn, [row["id"] for row in rows])
            messages = [
                self._message_dict(row, attachments.get(row["id"], []))
                for row in rows
            ]
            return {
                "messages": messages,
                "has_more": has_more,
                "project": self._project_dict(conn, project_row),
            }

    def create_message(
        self,
        project_id: str,
        body: Any,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_body = _clean_text(body, "Текст заметки", MAX_MESSAGE)
        files = files or []
        if not clean_body and not files:
            raise ValidationError("Добавьте текст или прикрепите файл")
        if len(files) > MAX_FILES:
            raise ValidationError(f"За один раз можно прикрепить не больше {MAX_FILES} файлов")
        total_size = 0
        prepared: list[dict[str, Any]] = []
        for item in files:
            filename = clean_filename(item.get("filename"))
            mime = str(item.get("mime") or "application/octet-stream")[:120]
            data = item.get("data")
            if not isinstance(data, (bytes, bytearray)):
                raise ValidationError("Некорректный файл")
            if len(data) > MAX_FILE_SIZE:
                raise ValidationError(
                    f"Файл «{filename}» больше {MAX_FILE_SIZE // (1024 * 1024)} МБ"
                )
            total_size += len(data)
            if total_size > MAX_FILES_TOTAL:
                raise ValidationError(
                    f"Суммарный размер файлов больше {MAX_FILES_TOTAL // (1024 * 1024)} МБ"
                )
            prepared.append(
                {
                    "id": new_id(),
                    "filename": filename,
                    "mime": mime,
                    "data": bytes(data),
                    "size": len(data),
                }
            )

        with self.write() as conn:
            project_row = self._project_row(conn, project_id)
            message_id = new_id()
            stamp = now_iso()
            conn.execute(
                "INSERT INTO messages(id, project_id, body, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_id, project_id, clean_body, stamp, stamp),
            )
            for file in prepared:
                conn.execute(
                    "INSERT INTO attachments(id, message_id, project_id, filename, mime, "
                    "size, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        file["id"],
                        message_id,
                        project_id,
                        file["filename"],
                        file["mime"],
                        file["size"],
                        sqlite3.Binary(file["data"]),
                        stamp,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            attachments = self._attachments_for(conn, [message_id])[message_id]
            message = self._message_dict(row, attachments)
            project = self._project_dict(conn, project_row)
            return {"message": message, "project": project}

    def update_message(self, message_id: str, body: Any) -> dict[str, Any]:
        clean_body = _clean_text(body, "Текст заметки", MAX_MESSAGE)
        with self.write() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Заметка не найдена")
            conn.execute(
                "UPDATE messages SET body = ?, updated_at = ? WHERE id = ?",
                (clean_body, now_iso(), message_id),
            )
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            attachments = self._attachments_for(conn, [message_id])[message_id]
            project = self._project_dict(
                conn, self._project_row(conn, row["project_id"])
            )
            return {
                "message": self._message_dict(row, attachments),
                "project": project,
            }

    def delete_message(self, message_id: str) -> dict[str, Any]:
        with self.write() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Заметка не найдена")
            project_id = row["project_id"]
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            project = self._project_dict(conn, self._project_row(conn, project_id))
            return {"ok": True, "project": project}

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Файл не найден")
            data = dict(row)
            data["data"] = bytes(row["data"])
            return data

    # ------------------------------------------------------------------
    # служебное
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self.read() as conn:
            def count(table: str) -> int:
                return int(
                    conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                )

            attachment_row = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS total FROM attachments"
            ).fetchone()
            return {
                "projects": count("projects"),
                "tasks": count("tasks"),
                "messages": count("messages"),
                "attachments": int(attachment_row["n"]),
                "attachment_bytes": int(attachment_row["total"]),
            }

    def info(self) -> dict[str, Any]:
        with self._lock:
            path = self.path
            exists = path.exists()
            try:
                stat = path.stat()
                size = stat.st_size
                modified_at = (
                    datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
            except FileNotFoundError:
                size = 0
                modified_at = None
            parent_writable = os.access(path.parent, os.W_OK)
            file_writable = os.access(path, os.W_OK) if exists else True
            conn = self._conn
            try:
                journal_mode = (
                    str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()
                    if conn is not None
                    else "CLOSED"
                )
            except sqlite3.Error:
                journal_mode = "UNKNOWN"
            return {
                "db": {
                    "path": str(path),
                    "data_dir": str(self.data_dir),
                    "backup_dir": str(self.backup_dir),
                    "size": size,
                    "modified_at": modified_at,
                    "exists": exists,
                    "writable": bool(parent_writable and file_writable),
                    "journal_mode": journal_mode,
                    "open": conn is not None,
                    "saved": conn is not None,
                    "last_write_at": self._last_write_at,
                    "revision": self._revision,
                },
                "stats": self.stats(),
                "version": SCHEMA_VERSION,
            }

    def backup_to(self, target: str | os.PathLike[str]) -> Path:
        """Сделать целостную копию базы (VACUUM INTO) в указанный файл."""
        target_path = Path(target).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            raise ValidationError("Файл резервной копии уже существует")
        with self._lock:
            conn = self._connection()
            conn.execute("VACUUM INTO ?", (str(target_path),))
        return target_path

    def create_backup(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        candidate = self.backup_dir / f"tasker-{stamp}.db"
        suffix = 2
        while candidate.exists():
            candidate = self.backup_dir / f"tasker-{stamp}-{suffix}.db"
            suffix += 1
        return self.backup_to(candidate)
