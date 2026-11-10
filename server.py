"""Tasker — локальный менеджер проектов, задач и заметок.

Запуск:
    python server.py
    Tasker.exe
    python server.py --port 8080 --db D:\\Sync\\tasker.db
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from datetime import datetime
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
from tasker import settings as app_settings  # noqa: E402

LOG_STREAM = None


def setup_logging() -> Path | None:
    """В windowed-сборке перенаправляет stdout/stderr в файл."""
    global LOG_STREAM
    if not FROZEN:
        return None
    try:
        log_dir = app_settings.config_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "tasker.log"
        try:
            if path.exists() and path.stat().st_size > 2_000_000:
                os.replace(path, path.with_name("tasker.previous.log"))
        except OSError:
            pass
        stream = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        LOG_STREAM = stream
        return path
    except Exception:  # noqa: BLE001
        try:
            stream = open(os.devnull, "w", encoding="utf-8")
            sys.stdout = stream
            sys.stderr = stream
            LOG_STREAM = stream
        except Exception:  # noqa: BLE001
            pass
        return None


def log(message: str) -> None:
    try:
        print(message)
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def report_error(message: str) -> None:
    log(f"ОШИБКА: {message}")
    if FROZEN and os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "Tasker", 0x10)
        except Exception:  # noqa: BLE001
            pass


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
        default=None,
        help="Путь к файлу базы. Без него используется путь из настроек "
        "или data/tasker.db рядом с приложением.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Не открывать браузер автоматически.",
    )
    parser.add_argument(
        "--auto-shutdown",
        action="store_true",
        help="Завершать сервер после закрытия последней вкладки. "
        "Включается автоматически, если Tasker открывает браузер.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Печатать все HTTP-запросы в консоль.",
    )
    return parser.parse_args(argv)


def resolve_db_path(raw: str, *, explicit: bool) -> Path:
    path = Path(os.path.expandvars(str(raw))).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() if explicit and not FROZEN else APP_DIR) / path
    return path.resolve()


def main(argv: list[str] | None = None) -> int:
    log_path = setup_logging()
    if FROZEN:
        log(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Tasker {VERSION}")
    set_console_title()
    args = parse_args(argv)
    auto_shutdown = args.auto_shutdown or not args.no_browser

    config_path = app_settings.settings_path()
    config = app_settings.load(config_path)
    explicit_db = args.db or os.environ.get("TASKER_DB")
    configured_db = config.get("db_path") if isinstance(config.get("db_path"), str) else None
    default_path = default_db_path()

    if explicit_db:
        db_path = resolve_db_path(explicit_db, explicit=True)
    elif configured_db:
        db_path = resolve_db_path(configured_db, explicit=False)
    else:
        db_path = default_path

    web_dir = RESOURCE_DIR / "web"
    if not (web_dir / "index.html").is_file():
        report_error(f"Не найден интерфейс: {web_dir / 'index.html'}")
        return 1

    try:
        db = Database(db_path)
    except Exception as exc:  # noqa: BLE001
        if configured_db and not explicit_db:
            log(f"Не удалось открыть сохранённую базу {db_path}: {exc}")
            log(f"Использую базу по умолчанию: {default_path}")
            try:
                db = Database(default_path)
                db_path = default_path
            except Exception as fallback_exc:  # noqa: BLE001
                report_error(
                    f"Не удалось открыть базу по умолчанию: {fallback_exc}"
                )
                return 1
        else:
            report_error(
                f"Не удалось открыть базу данных: {exc}\n\n"
                "Разместите Tasker.exe в папке с правами на запись "
                "или укажите --db на другой путь."
            )
            return 1

    server = None
    selected_port = None
    last_error: OSError | None = None
    for port in range(args.port, args.port + 30):
        if not port_is_free(args.host, port):
            continue
        try:
            server = create_server(
                args.host,
                port,
                db,
                web_dir,
                args.verbose,
                app_dir=APP_DIR,
                default_db_path=default_path,
                settings_path=config_path,
                auto_shutdown=auto_shutdown,
            )
            selected_port = port
            break
        except OSError as exc:
            last_error = exc
            continue

    if server is None or selected_port is None:
        db.close()
        report_error(
            "Не удалось найти свободный порт"
            + (f": {last_error}" if last_error else ".")
        )
        return 1

    host_for_url = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{host_for_url}:{selected_port}/"

    log("")
    log(f"  Tasker {VERSION}" + (" (portable)" if FROZEN else ""))
    log("  ------------------------------------------------")
    log(f"  Интерфейс:  {url}")
    log(f"  База:       {db.path}")
    log(f"  Настройки:  {config_path}")
    if log_path:
        log(f"  Журнал:     {log_path}")
    log("  Данные:     один файл SQLite, вложения внутри базы")
    if auto_shutdown:
        log("  Остановка:  закройте вкладку браузера")
    else:
        log("  Остановка:  Ctrl+C")
    log("")
    if selected_port != args.port:
        log(f"  Порт {args.port} занят, используется {selected_port}.")
        log("")

    if not args.no_browser:
        def open_browser() -> None:
            try:
                opened = webbrowser.open(url, new=2)
            except Exception as exc:  # noqa: BLE001
                opened = False
                report_error(f"Не удалось открыть браузер: {exc}\n\nОткройте {url}")
                return
            if not opened:
                report_error(
                    "Не удалось открыть браузер автоматически.\n\n"
                    f"Откройте вручную: {url}"
                )

        threading.Timer(0.6, open_browser).start()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        log("\nОстановка Tasker…")
    finally:
        try:
            server.server_close()
        finally:
            db.close()
            log("Tasker остановлен; база закрыта.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
