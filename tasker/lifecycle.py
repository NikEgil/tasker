"""Завершение Tasker после закрытия вкладки браузера."""

from __future__ import annotations

import threading
import time
from typing import Callable


class BrowserLifecycle:
    """Хранит открытые вкладки и завершает сервер, когда их не осталось.

    Страница регистрируется через ``open`` и снимается через ``close``.
    После последней вкладки даётся небольшая пауза, чтобы перезагрузка
    страницы успела открыть новую сессию и не выключить сервер.
    """

    def __init__(
        self,
        shutdown: Callable[[], None],
        *,
        enabled: bool = False,
        grace: float = 4.0,
        startup_timeout: float = 120.0,
    ):
        self._shutdown = shutdown
        self._enabled = enabled
        self._grace = grace
        self._startup_timeout = startup_timeout
        self._lock = threading.RLock()
        self._sessions: set[str] = set()
        self._ever_seen = False
        self._deadline: float | None = None
        self._stopped = False
        self._thread: threading.Thread | None = None
        if enabled:
            self._thread = threading.Thread(
                target=self._run, name="tasker-lifecycle", daemon=True
            )
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stopping(self) -> bool:
        with self._lock:
            return self._stopped

    def open(self, session_id: str | None) -> None:
        if not self._enabled or not session_id:
            return
        with self._lock:
            if self._stopped:
                return
            self._sessions.add(session_id)
            self._ever_seen = True
            self._deadline = None

    def close(self, session_id: str | None) -> None:
        if not self._enabled or not session_id:
            return
        with self._lock:
            if self._stopped:
                return
            # Даже неизвестный id означает, что вкладка существовала:
            # это важно, если страница закрылась до регистрации.
            first_seen = not self._ever_seen
            self._ever_seen = True
            removed = session_id in self._sessions
            if removed:
                self._sessions.remove(session_id)
            if not self._sessions and (removed or first_seen):
                self._deadline = time.monotonic() + self._grace

    def stop(self) -> None:
        with self._lock:
            self._stopped = True

    def _run(self) -> None:
        started = time.monotonic()
        while True:
            with self._lock:
                if self._stopped:
                    return
                now = time.monotonic()
                should_shutdown = False
                if not self._ever_seen:
                    should_shutdown = now - started >= self._startup_timeout
                elif not self._sessions and self._deadline is not None:
                    should_shutdown = now >= self._deadline
                if should_shutdown:
                    self._stopped = True
            if should_shutdown:
                try:
                    self._shutdown()
                except Exception:  # noqa: BLE001
                    pass
                return
            time.sleep(0.25)
