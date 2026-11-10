"""Tasker — локальный менеджер проектов, задач и заметок.

Запуск:
    python server.py
    python server.py --port 8080 --db D:\\Sync\\tasker.db
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else BASE_DIR
sys.path.insert(0, str(BASE_DIR))

if sys.version_info < (3, 10):
    sys.exit("Tasker требует Python 3.10 или новее.")

from tasker.db import Database  # noqa: E402
from tasker.http import VERSION, create_server  # noqa: E402


def set_console_title() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW("Tasker")
    except Exception:  # noqa: BLE001
        pass


def default_db_path() -> Path:
    return APP_DIR / "data" / "tasker.db"


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tasker — локальные проекты, задачи и заметки в одном файле БД."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Tasker {VERSION}",
        help="Показать версию и выйти.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("TASKER_HOST", "127.0.0.1"),
        help="Адрес сервера (по умолчанию 127.0.0.1, только этот компьютер).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TASKER_PORT", "8765")),
        help="Порт (по умолчанию 8765; если занят, будет выбран следующий).",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TASKER_DB", str(default_db_path())),
        help="Путь к файлу базы данных.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Не открывать браузер автоматически.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Печатать все HTTP-запросы в консоль.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    set_console_title()
    args = parse_args(argv)

    db_path = Path(args.db).expanduser()
    if FROZEN and not db_path.is_absolute():
        db_path = APP_DIR / db_path
    web_dir = RESOURCE_DIR / "web"
    if not (web_dir / "index.html").is_file():
        print(f"Не найден интерфейс: {web_dir / 'index.html'}", file=sys.stderr)
        return 1

    try:
        db = Database(db_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось открыть базу данных: {exc}", file=sys.stderr)
        if FROZEN:
            print(
                "Разместите Tasker.exe в папке с правами на запись "
                "или укажите --db на другой путь.",
                file=sys.stderr,
            )
        return 1

    server = None
    selected_port = None
    last_error: OSError | None = None
    for port in range(args.port, args.port + 30):
        if not port_is_free(args.host, port):
            continue
        try:
            server = create_server(args.host, port, db, web_dir, args.verbose)
            selected_port = port
            break
        except OSError as exc:
            last_error = exc
            continue

    if server is None or selected_port is None:
        print(
            "Не удалось найти свободный порт"
            + (f": {last_error}" if last_error else "."),
            file=sys.stderr,
        )
        return 1

    host_for_url = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{host_for_url}:{selected_port}/"

    print("")
    print(f"  Tasker {VERSION}" + (" · portable" if FROZEN else ""))
    print("  ------------------------------------------------")
    print(f"  Интерфейс:  {url}")
    print(f"  База:       {db.path}")
    print("  Данные:     один файл SQLite, вложения внутри базы")
    print("  Остановка:  Ctrl+C")
    print("")
    if selected_port != args.port:
        print(f"  Порт {args.port} занят, используется {selected_port}.")
        print("")

    if not args.no_browser:
        def open_browser() -> None:
            try:
                webbrowser.open(url, new=2)
            except Exception:  # noqa: BLE001
                pass

        threading.Timer(0.6, open_browser).start()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nОстановка Tasker…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
