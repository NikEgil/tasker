import { api } from "./api.js";

function createSessionId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Держит SSE-канал вкладки. Сервер видит обрыв соединения и завершает
 * работу, когда закрыта последняя вкладка. Дополнительно при выгрузке
 * страницы отправляется короткий beacon.
 */
export function startBrowserLifecycle() {
  const sessionId = createSessionId();
  let closed = false;
  let source = null;

  const connect = () => {
    if (closed) return;
    if (!window.EventSource) {
      // Очень старый браузер: остаётся только регистрация и beacon.
      api.sessionOpen(sessionId).catch(() => {});
      return;
    }
    try {
      source = new EventSource(
        `/api/session/events?session_id=${encodeURIComponent(sessionId)}`
      );
    } catch (error) {
      source = null;
    }
  };

  const close = () => {
    if (closed) return;
    closed = true;
    if (source) {
      source.close();
      source = null;
    }
    const url = `/api/session/close?session_id=${encodeURIComponent(sessionId)}`;
    try {
      fetch(url, {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }).catch(() => {});
    } catch (error) {
      /* ignore */
    }
  };

  window.addEventListener("pagehide", (event) => {
    if (!event.persisted) close();
  });
  window.addEventListener("beforeunload", close);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      closed = false;
      if (!source || source.readyState === EventSource.CLOSED) connect();
    }
  });

  connect();
}
