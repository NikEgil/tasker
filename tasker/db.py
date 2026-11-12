"""SQLite-хранилище Tasker.

Проекты, задачи и заметки лежат в текстовой базе данных, а вложения — в
отдельной папке рядом с ней. Это позволяет синхронизировать только текст и
скачивать тяжёлые файлы по необходимости.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 5


def files_dir_for_db(path: str | os.PathLike[str]) -> Path:
    """Папка с вложениями, расположенная рядом с файлом базы."""
    database = Path(path).expanduser().resolve()
    return database.parent / f"{database.stem}_files"

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
    scheduled_date TEXT,
    scheduled_end_date TEXT,
    calendar_order INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id           TEXT PRIMARY KEY,
    message_id   TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    mime         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    created_at   TEXT NOT NULL
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


def _clean_scheduled_range(
    start: Any, end: Any
) -> tuple[str | None, str | None]:
    """Проверить диапазон задачи; одиночный день хранится как NULL."""
    clean_start = _clean_scheduled_date(start)
    clean_end = _clean_scheduled_date(end)
    if clean_start is None:
        if clean_end is not None:
            raise ValidationError("Дата окончания указана без даты начала")
        return None, None
    if clean_end is not None:
        start_day = date.fromisoformat(clean_start)
        end_day = date.fromisoformat(clean_end)
        if end_day < start_day:
            raise ValidationError("Дата окончания раньше даты начала")
        if (end_day - start_day).days > 366:
            raise ValidationError("Задача не может длиться больше года")
        if end_day == start_day:
            clean_end = None
    return clean_start, clean_end


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
        files_dir = files_dir_for_db(target)
        self._backup_before_migration(target)
        conn = self._open_connection(target)
        try:
            self._init_schema(conn, files_dir)
        except BaseException:
            conn.close()
            raise
        self.path = target
        self.data_dir = target.parent
        self.files_dir = files_dir
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
    def _backup_before_migration(target: Path) -> None:
        """Перед переносом BLOB-вложений сохранить исходный файл базы."""
        if not target.is_file() or target.stat().st_size == 0:
            return
        try:
            conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "attachments" not in tables:
                    return
                columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(attachments)")
                }
                if "data" not in columns:
                    return
            finally:
                conn.close()
            backup_dir = target.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            candidate = backup_dir / f"pre-v3-{stamp}.db"
            suffix = 2
            while candidate.exists():
                candidate = backup_dir / f"pre-v3-{stamp}-{suffix}.db"
                suffix += 1
            shutil.copy2(target, candidate)
        except (OSError, sqlite3.Error):
            # Миграция всё равно должна попробовать выполниться.
            return

    @staticmethod
    def _init_schema(conn: sqlite3.Connection, files_dir: Path) -> None:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Мягкая миграция: старые базы получают новые поля без перезаписи.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "scheduled_date" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN scheduled_date TEXT")
            if "scheduled_end_date" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN scheduled_end_date TEXT")
            if "calendar_order" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN calendar_order INTEGER")
            # Старые задачи получают порядок один раз: длинные полосы выше,
            # дальше — по дате. Дальше порядок меняется только вручную.
            pending = conn.execute(
                "SELECT id, scheduled_end_date, scheduled_date, status, position, created_at "
                "FROM tasks WHERE scheduled_date IS NOT NULL AND calendar_order IS NULL "
                "ORDER BY CASE WHEN scheduled_end_date IS NOT NULL "
                "AND scheduled_end_date > scheduled_date THEN 0 ELSE 1 END, "
                "scheduled_date, CASE status WHEN 'urgent' THEN 0 WHEN 'later' THEN 1 ELSE 2 END, "
                "position, created_at"
            ).fetchall()
            for order, row in enumerate(pending):
                conn.execute(
                    "UPDATE tasks SET calendar_order = ? WHERE id = ?",
                    (order, row["id"]),
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_scheduled_end "
                "ON tasks(scheduled_end_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_calendar_order "
                "ON tasks(calendar_order)"
            )
            Database._migrate_attachments(conn, files_dir)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    @staticmethod
    def _migrate_attachments(conn: sqlite3.Connection, files_dir: Path) -> None:
        """Перенести старые BLOB-вложения в папку рядом с базой."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(attachments)")
        }
        if "storage_path" in columns and "data" not in columns:
            return

        files_dir.mkdir(parents=True, exist_ok=True)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments_v3 (
                id           TEXT PRIMARY KEY,
                message_id   TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename     TEXT NOT NULL,
                mime         TEXT NOT NULL,
                size         INTEGER NOT NULL,
                storage_path TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
            """
        )
        rows = conn.execute(
            "SELECT id, message_id, project_id, filename, mime, size, "
            f"{'storage_path' if 'storage_path' in columns else 'NULL AS storage_path'}, "
            f"{'data' if 'data' in columns else 'NULL AS data'}, created_at "
            "FROM attachments"
        ).fetchall()
        for row in rows:
            relative = str(row["storage_path"] or "")
            if not relative:
                relative = f"{row['id'][:2]}/{row['id']}.bin"
                target = files_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = row["data"]
                if payload is None:
                    payload = b""
                temporary = target.with_name(target.name + f".tmp-{uuid.uuid4().hex}")
                try:
                    temporary.write_bytes(bytes(payload))
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            conn.execute(
                "INSERT OR REPLACE INTO attachments_v3"
                "(id, message_id, project_id, filename, mime, size, storage_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["message_id"],
                    row["project_id"],
                    row["filename"],
                    row["mime"],
                    row["size"],
                    relative,
                    row["created_at"],
                ),
            )
        conn.execute("DROP TABLE attachments")
        conn.execute("ALTER TABLE attachments_v3 RENAME TO attachments")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachments_message "
            "ON attachments(message_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attachments_project "
            "ON attachments(project_id)"
        )

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
            self._backup_before_migration(target)
            new_conn = self._open_connection(target)
            new_files_dir = files_dir_for_db(target)
            try:
                self._init_schema(new_conn, new_files_dir)
            except BaseException:
                new_conn.close()
                raise
            old_conn = self._conn
            self.path = target
            self.data_dir = target.parent
            self.files_dir = new_files_dir
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
            paths = [
                row["storage_path"]
                for row in conn.execute(
                    "SELECT storage_path FROM attachments WHERE project_id = ?",
                    (project_id,),
                )
            ]
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._remove_attachment_files(paths)

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
            "scheduled_end_date": row["scheduled_end_date"],
            "calendar_order": row["calendar_order"],
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
                "AND t.scheduled_date <= ? "
                "AND COALESCE(t.scheduled_end_date, t.scheduled_date) >= ? "
                "ORDER BY t.scheduled_date, "
                "CASE t.status WHEN 'urgent' THEN 0 WHEN 'later' THEN 1 ELSE 2 END, "
                "t.position, t.created_at",
                (end, start),
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

    @staticmethod
    def _calendar_order_for(conn: sqlite3.Connection, multi: bool) -> int:
        if multi:
            row = conn.execute(
                "SELECT COALESCE(MIN(calendar_order), 0) - 1 AS o "
                "FROM tasks WHERE scheduled_date IS NOT NULL"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(MAX(calendar_order), -1) + 1 AS o "
                "FROM tasks WHERE scheduled_date IS NOT NULL"
            ).fetchone()
        return int(row["o"])

    def create_task(
        self,
        project_id: str,
        title: Any,
        description: Any = "",
        status: Any = "urgent",
        scheduled_date: Any = None,
        scheduled_end_date: Any = None,
    ) -> dict[str, Any]:
        clean_title = _clean_title(title, "Название задачи", MAX_TITLE)
        clean_description = _clean_text(description, "Описание", MAX_DESCRIPTION)
        clean_status = _clean_status(status)
        clean_date, clean_end_date = _clean_scheduled_range(
            scheduled_date, scheduled_end_date
        )
        with self.write() as conn:
            self._project_row(conn, project_id)
            position_row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM tasks "
                "WHERE project_id = ? AND status = ?",
                (project_id, clean_status),
            ).fetchone()
            position = int(position_row["p"])
            calendar_order = (
                None
                if clean_date is None
                else self._calendar_order_for(conn, clean_end_date is not None)
            )
            task_id = new_id()
            stamp = now_iso()
            conn.execute(
                "INSERT INTO tasks(id, project_id, title, description, status, position, "
                "created_at, updated_at, done_at, scheduled_date, scheduled_end_date, "
                "calendar_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    clean_end_date,
                    calendar_order,
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

    def reorder_calendar(self, task_ids: Any) -> None:
        """Задать ручной вертикальный порядок задач в календаре."""
        if not isinstance(task_ids, list):
            raise ValidationError("Ожидается список задач")
        if len(task_ids) > 5000:
            raise ValidationError("Слишком много задач")
        clean_ids: list[str] = []
        for value in task_ids:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
                raise ValidationError("Некорректный идентификатор задачи")
            if value in clean_ids:
                raise ValidationError("Задача указана дважды")
            clean_ids.append(value)
        with self.write() as conn:
            for task_id in clean_ids:
                self._task_row(conn, task_id)
            for order, task_id in enumerate(clean_ids):
                conn.execute(
                    "UPDATE tasks SET calendar_order = ? WHERE id = ?",
                    (order, task_id),
                )

    def update_task(
        self,
        task_id: str,
        title: Any = UNSET,
        description: Any = UNSET,
        status: Any = UNSET,
        scheduled_date: Any = UNSET,
        scheduled_end_date: Any = UNSET,
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
            old_start = row["scheduled_date"]
            old_end = row["scheduled_end_date"] or old_start
            if scheduled_date is UNSET and scheduled_end_date is UNSET:
                new_scheduled_date = old_start
                new_end_date = row["scheduled_end_date"]
            elif scheduled_date is UNSET:
                new_scheduled_date = old_start
                new_end_date = _clean_scheduled_date(scheduled_end_date)
                new_scheduled_date, new_end_date = _clean_scheduled_range(
                    new_scheduled_date, new_end_date
                )
            elif scheduled_end_date is UNSET:
                new_scheduled_date, _ = _clean_scheduled_range(scheduled_date, None)
                if new_scheduled_date is None:
                    new_end_date = None
                else:
                    # При переносе только начала сохраняем прежнюю длительность.
                    duration = 0
                    if old_start and old_end:
                        duration = (
                            date.fromisoformat(old_end) - date.fromisoformat(old_start)
                        ).days
                    shifted = (
                        date.fromisoformat(new_scheduled_date)
                        + timedelta(days=duration)
                    ).isoformat()
                    new_scheduled_date, new_end_date = _clean_scheduled_range(
                        new_scheduled_date, shifted
                    )
            else:
                new_scheduled_date, new_end_date = _clean_scheduled_range(
                    scheduled_date, scheduled_end_date
                )
            new_calendar_order = row["calendar_order"]
            if (
                old_start is None
                and new_scheduled_date is not None
                and row["calendar_order"] is None
            ):
                new_calendar_order = self._calendar_order_for(
                    conn, new_end_date is not None
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
                "scheduled_date = ?, scheduled_end_date = ?, calendar_order = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    new_title,
                    new_description,
                    done_at,
                    new_scheduled_date,
                    new_end_date,
                    new_calendar_order,
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

    def _attachment_path(self, storage_path: str) -> Path:
        relative = Path(storage_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise NotFoundError("Файл не найден")
        target = (self.files_dir / relative).resolve()
        try:
            target.relative_to(self.files_dir.resolve())
        except ValueError:
            raise NotFoundError("Файл не найден") from None
        if not target.is_file():
            raise NotFoundError("Файл не найден")
        return target

    def _write_attachment_file(self, attachment_id: str, data: bytes) -> Path:
        relative = Path(attachment_id[:2]) / f"{attachment_id}.bin"
        target = self.files_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + f".tmp-{uuid.uuid4().hex}")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _remove_attachment_files(self, storage_paths: list[str]) -> None:
        for storage_path in storage_paths:
            try:
                self._attachment_path(storage_path).unlink(missing_ok=True)
            except (NotFoundError, OSError):
                continue

    def attachment_files(self) -> list[Path]:
        """Все существующие файлы, на которые ссылается база."""
        with self.read() as conn:
            rows = conn.execute(
                "SELECT storage_path FROM attachments ORDER BY id"
            ).fetchall()
        result = []
        for row in rows:
            try:
                result.append(self._attachment_path(row["storage_path"]))
            except NotFoundError:
                continue
        return result

    def copy_attachment_files(self, destination: Path) -> int:
        """Скопировать файлы вложений в указанную папку, сохраняя пути."""
        destination.mkdir(parents=True, exist_ok=True)
        with self.read() as conn:
            rows = conn.execute(
                "SELECT storage_path FROM attachments ORDER BY id"
            ).fetchall()
        copied = 0
        for row in rows:
            source = self._attachment_path(row["storage_path"])
            target = destination / Path(row["storage_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            copied += 1
        return copied

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
            written: list[Path] = []
            try:
                for file in prepared:
                    storage_path = self._write_attachment_file(
                        file["id"], file["data"]
                    )
                    written.append(storage_path)
                    conn.execute(
                        "INSERT INTO attachments(id, message_id, project_id, filename, mime, "
                        "size, storage_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            file["id"],
                            message_id,
                            project_id,
                            file["filename"],
                            file["mime"],
                            file["size"],
                            storage_path.relative_to(self.files_dir).as_posix(),
                            stamp,
                        ),
                    )
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
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
            paths = [
                item["storage_path"]
                for item in conn.execute(
                    "SELECT storage_path FROM attachments WHERE message_id = ?",
                    (message_id,),
                )
            ]
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            project = self._project_dict(conn, self._project_row(conn, project_id))
            result = {"ok": True, "project": project}
        self._remove_attachment_files(paths)
        return result

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Файл не найден")
            data = dict(row)
            data["data"] = self._attachment_path(row["storage_path"]).read_bytes()
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
                    "files_dir": str(self.files_dir),
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

    def replace_from_snapshot(
        self,
        source_db: str | os.PathLike[str],
        source_files: str | os.PathLike[str] | None,
    ) -> dict[str, Any]:
        """Заменить локальную базу и папку файлов данными с Диска."""
        source = Path(source_db).expanduser().resolve()
        files_source = (
            Path(source_files).expanduser().resolve()
            if source_files
            else None
        )
        self.inspect_path(source)
        if not source.is_file():
            raise ValidationError("Файл базы с Диска не найден")

        with self._lock:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            backup_db = self.backup_dir / f"sync-before-{stamp}.db"
            self.backup_to(backup_db)

            old_files = None
            if self.files_dir.exists():
                old_files = self.backup_dir / f"sync-before-{stamp}-files"
                suffix = 2
                while old_files.exists():
                    old_files = self.backup_dir / f"sync-before-{stamp}-files-{suffix}"
                    suffix += 1
                os.replace(self.files_dir, old_files)

            staging_files = self.files_dir.with_name(
                f".{self.files_dir.name}.incoming-{uuid.uuid4().hex}"
            )
            try:
                source_conn = sqlite3.connect(
                    f"{source.as_uri()}?mode=ro", uri=True, timeout=20
                )
                try:
                    source_conn.backup(self._connection())
                finally:
                    source_conn.close()
                if files_source is not None and files_source.is_dir():
                    shutil.copytree(files_source, staging_files)
                else:
                    staging_files.mkdir(parents=True, exist_ok=True)
                os.replace(staging_files, self.files_dir)
                self._init_schema(self._connection(), self.files_dir)
                missing = [
                    relative
                    for (relative,) in self._connection().execute(
                        "SELECT storage_path FROM attachments"
                    )
                    if not (self.files_dir / relative).is_file()
                ]
                if missing:
                    raise ValidationError(
                        "В базе с Диска не хватает файлов вложений"
                    )
                self._revision = 0
                self._last_write_at = None
            except BaseException:
                if staging_files.exists():
                    shutil.rmtree(staging_files, ignore_errors=True)
                # Попытаться вернуть прежнюю базу и файлы.
                try:
                    if backup_db.exists():
                        restore_conn = sqlite3.connect(
                            f"{backup_db.as_uri()}?mode=ro", uri=True, timeout=20
                        )
                        try:
                            restore_conn.backup(self._connection())
                        finally:
                            restore_conn.close()
                    if old_files is not None and old_files.exists():
                        if self.files_dir.exists():
                            shutil.rmtree(self.files_dir, ignore_errors=True)
                        os.replace(old_files, self.files_dir)
                except BaseException:
                    pass
                raise
        return self.info()

    def backup_archive_to(self, target: str | os.PathLike[str]) -> Path:
        """Сохранить базу и папку файлов в один ZIP-архив."""
        target_path = Path(target).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            raise ValidationError("Файл резервной копии уже существует")
        with tempfile.TemporaryDirectory(prefix="tasker-archive-") as tmp:
            snapshot = Path(tmp) / "tasker.db"
            self.backup_to(snapshot)
            with zipfile.ZipFile(
                target_path, "w", compression=zipfile.ZIP_STORED
            ) as archive:
                archive.write(snapshot, "tasker.db")
                with self.read() as conn:
                    rows = conn.execute(
                        "SELECT storage_path FROM attachments ORDER BY id"
                    ).fetchall()
                for row in rows:
                    relative = Path(row["storage_path"])
                    source = self._attachment_path(row["storage_path"])
                    archive.write(source, f"tasker_files/{relative.as_posix()}")
        return target_path

    def create_backup(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        candidate = self.backup_dir / f"tasker-{stamp}.zip"
        suffix = 2
        while candidate.exists():
            candidate = self.backup_dir / f"tasker-{stamp}-{suffix}.zip"
            suffix += 1
        return self.backup_archive_to(candidate)

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

